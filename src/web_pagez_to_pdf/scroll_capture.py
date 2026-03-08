"""Browser-window auto-scroll capture orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageQt

from .stitching import frame_diff_score, stitch_frames

if TYPE_CHECKING:
    from collections.abc import Callable

    from .capture_service import WindowCaptureService

DEFAULT_SCROLL_STRATEGY = "hybrid_wheel_pagedown"
SCROLL_STRATEGIES = (
    DEFAULT_SCROLL_STRATEGY,
    "pagedown_only",
    "wheel_only",
)
DEFAULT_WHEEL_INJECTION_MODE = "physical_center_sendinput"
WHEEL_INJECTION_MODES = (
    DEFAULT_WHEEL_INJECTION_MODE,
    "legacy_message_wheel",
)
DEFAULT_CENTER_CLICK_ASSIST = "on_no_movement"
CENTER_CLICK_ASSIST_MODES = (
    "off",
    DEFAULT_CENTER_CLICK_ASSIST,
)
DEFAULT_CURSOR_HOLD_MODE = "keep_at_center"
CURSOR_HOLD_MODES = (
    DEFAULT_CURSOR_HOLD_MODE,
    "restore_each_step",
)
STOP_REASON_RUNNING = "running"
STOP_REASON_USER = "user_stop"
STOP_REASON_REPEAT = "repeat_detected"
STOP_REASON_MAX_PAGES = "max_pages"
STOP_REASON_CAPTURE_FAILED = "capture_failed"


@dataclass(slots=True)
class ScrollCaptureOptions:
    """Configuration for full-page browser auto-scroll capture."""

    delay_ms: int = 380
    max_capture_pages: int = 18
    capture_backend: str = "screen_region_gdi"
    scroll_strategy: str = DEFAULT_SCROLL_STRATEGY
    wheel_injection_mode: str = DEFAULT_WHEEL_INJECTION_MODE
    center_click_assist: str = DEFAULT_CENTER_CLICK_ASSIST
    cursor_hold_mode: str = DEFAULT_CURSOR_HOLD_MODE
    repeated_frame_score_threshold: float = 1.8
    repeated_frame_stop_count: int = 2


@dataclass(slots=True)
class ScrollCaptureProgress:
    """Per-step telemetry emitted while full capture is running."""

    frame_index: int
    backend_used: str
    scroll_method: str
    diff_score: float | None
    repeated_count: int
    stop_reason: str
    message: str


@dataclass(slots=True)
class ScrollCaptureResult:
    """Result payload from full-page capture."""

    image: Image.Image
    captured_frames: int
    ended_by_repeat: bool
    stop_reason: str


def run_full_page_capture(
    service: WindowCaptureService,
    target_hwnd: int,
    options: ScrollCaptureOptions,
    stop_requested: Callable[[], bool],
    progress_callback: Callable[[ScrollCaptureProgress], None] | None = None,
) -> ScrollCaptureResult:
    """Capture browser frames while scrolling and then stitch."""

    activated, reason = service.activate_window(target_hwnd)
    if not activated:
        raise RuntimeError(reason or "Could not bring selected window to foreground.")

    max_pages = max(1, int(options.max_capture_pages))
    threshold = max(0.1, float(options.repeated_frame_score_threshold))
    repeat_stop = max(1, int(options.repeated_frame_stop_count))
    delay_ms = max(120, int(options.delay_ms))
    scroll_strategy = _normalize_scroll_strategy(options.scroll_strategy)
    wheel_mode = _normalize_wheel_injection_mode(options.wheel_injection_mode)
    click_assist = _normalize_center_click_assist(options.center_click_assist)
    cursor_hold_mode = _normalize_cursor_hold_mode(options.cursor_hold_mode)

    service.start_full_capture_input_session(
        target_hwnd,
        cursor_hold_mode=cursor_hold_mode,
    )
    try:
        first_frame, first_backend = _capture_frame(
            service,
            target_hwnd,
            options.capture_backend,
        )
        if first_frame is None:
            raise RuntimeError("No frames were captured.")

        frames: list[Image.Image] = [first_frame]
        repeated_count = 0
        stop_reason = STOP_REASON_RUNNING
        _emit_progress(
            progress_callback,
            ScrollCaptureProgress(
                frame_index=1,
                backend_used=first_backend,
                scroll_method="initial",
                diff_score=None,
                repeated_count=0,
                stop_reason=STOP_REASON_RUNNING,
                message=f"Frame 1 captured via {first_backend or 'unknown backend'} (initial).",
            ),
        )

        while len(frames) < max_pages:
            if stop_requested():
                stop_reason = STOP_REASON_USER
                break

            focused, focus_reason = service.ensure_window_foreground(target_hwnd)
            if not focused:
                stop_reason = STOP_REASON_CAPTURE_FAILED
                _emit_progress(
                    progress_callback,
                    ScrollCaptureProgress(
                        frame_index=len(frames),
                        backend_used="",
                        scroll_method="focus",
                        diff_score=None,
                        repeated_count=repeated_count,
                        stop_reason=stop_reason,
                        message=focus_reason or "Could not refocus target window.",
                    ),
                )
                break

            attempted_frame_index = len(frames) + 1
            frame, backend_used, scroll_method = _capture_after_scroll_ladder(
                service=service,
                target_hwnd=target_hwnd,
                previous_frame=frames[-1],
                delay_ms=delay_ms,
                capture_backend=options.capture_backend,
                scroll_strategy=scroll_strategy,
                wheel_mode=wheel_mode,
                click_assist=click_assist,
                cursor_hold_mode=cursor_hold_mode,
                threshold=threshold,
            )
            if frame is None:
                stop_reason = STOP_REASON_CAPTURE_FAILED
                _emit_progress(
                    progress_callback,
                    ScrollCaptureProgress(
                        frame_index=attempted_frame_index,
                        backend_used=backend_used,
                        scroll_method=scroll_method,
                        diff_score=None,
                        repeated_count=repeated_count,
                        stop_reason=stop_reason,
                        message=(
                            f"Frame {attempted_frame_index} capture failed "
                            f"after scroll={scroll_method}."
                        ),
                    ),
                )
                break

            diff_score = frame_diff_score(frames[-1], frame)
            if diff_score <= threshold:
                repeated_count += 1
                message = (
                    f"Frame {attempted_frame_index}: no movement (scroll={scroll_method}, "
                    f"diff={diff_score:.2f}, repeat={repeated_count}/{repeat_stop})."
                )
                if repeated_count >= repeat_stop:
                    stop_reason = STOP_REASON_REPEAT
                    _emit_progress(
                        progress_callback,
                        ScrollCaptureProgress(
                            frame_index=attempted_frame_index,
                            backend_used=backend_used,
                            scroll_method=scroll_method,
                            diff_score=diff_score,
                            repeated_count=repeated_count,
                            stop_reason=stop_reason,
                            message=message,
                        ),
                    )
                    break
            else:
                repeated_count = 0
                message = (
                    f"Frame {attempted_frame_index} captured via {backend_used or 'unknown backend'} "
                    f"(scroll={scroll_method}, diff={diff_score:.2f})."
                )

            frames.append(frame)
            _emit_progress(
                progress_callback,
                ScrollCaptureProgress(
                    frame_index=len(frames),
                    backend_used=backend_used,
                    scroll_method=scroll_method,
                    diff_score=diff_score,
                    repeated_count=repeated_count,
                    stop_reason=STOP_REASON_RUNNING,
                    message=message,
                ),
            )

        if stop_reason == STOP_REASON_RUNNING:
            stop_reason = STOP_REASON_MAX_PAGES
        if not frames:
            raise RuntimeError("No frames were captured.")

        stitched = stitch_frames(frames).image
        return ScrollCaptureResult(
            image=stitched,
            captured_frames=len(frames),
            ended_by_repeat=stop_reason == STOP_REASON_REPEAT,
            stop_reason=stop_reason,
        )
    finally:
        service.end_full_capture_input_session()


def _capture_after_scroll_ladder(
    *,
    service: WindowCaptureService,
    target_hwnd: int,
    previous_frame: Image.Image,
    delay_ms: int,
    capture_backend: str,
    scroll_strategy: str,
    wheel_mode: str,
    click_assist: str,
    cursor_hold_mode: str,
    threshold: float,
) -> tuple[Image.Image | None, str, str]:
    scroll_method = "wheel_center"
    if scroll_strategy == "pagedown_only":
        service.send_page_down()
        service.wait_after_scroll(delay_ms)
        frame, backend = _capture_frame(service, target_hwnd, capture_backend)
        return (frame, backend, "pagedown")

    service.wheel_down_at_window_center(
        target_hwnd,
        wheel_injection_mode=wheel_mode,
        cursor_hold_mode=cursor_hold_mode,
    )
    service.wait_after_scroll(delay_ms)
    frame, backend = _capture_frame(service, target_hwnd, capture_backend)
    if frame is None:
        return (None, backend, scroll_method)
    diff_score = frame_diff_score(previous_frame, frame)
    if diff_score > threshold:
        return (frame, backend, scroll_method)

    if click_assist == "on_no_movement":
        service.click_window_center(target_hwnd, cursor_hold_mode=cursor_hold_mode)
        service.wheel_down_at_window_center(
            target_hwnd,
            wheel_injection_mode=wheel_mode,
            cursor_hold_mode=cursor_hold_mode,
        )
        service.wait_after_scroll(delay_ms)
        frame_after_click, backend_after_click = _capture_frame(
            service, target_hwnd, capture_backend
        )
        scroll_method = "click_center_then_wheel"
        if frame_after_click is None:
            return (None, backend_after_click, scroll_method)
        frame = frame_after_click
        backend = backend_after_click or backend
        diff_score = frame_diff_score(previous_frame, frame)
        if diff_score > threshold or scroll_strategy == "wheel_only":
            return (frame, backend, scroll_method)
    elif scroll_strategy == "wheel_only":
        return (frame, backend, scroll_method)

    if scroll_strategy == DEFAULT_SCROLL_STRATEGY:
        service.send_page_down()
        service.wait_after_scroll(delay_ms)
        frame_after_page, backend_after_page = _capture_frame(
            service, target_hwnd, capture_backend
        )
        scroll_method = "pagedown"
        if frame_after_page is None:
            return (None, backend_after_page, scroll_method)
        return (frame_after_page, backend_after_page or backend, scroll_method)

    return (frame, backend, scroll_method)


def _capture_frame(
    service: WindowCaptureService,
    target_hwnd: int,
    capture_backend: str,
) -> tuple[Image.Image | None, str]:
    pixmap, backend_used = service.capture_window(
        target_hwnd,
        primary_backend=capture_backend,
    )
    if pixmap is None:
        return (None, backend_used)
    return (ImageQt.fromqpixmap(pixmap).convert("RGB"), backend_used)


def _emit_progress(
    callback: Callable[[ScrollCaptureProgress], None] | None,
    payload: ScrollCaptureProgress,
) -> None:
    if callback is None:
        return
    callback(payload)


def _normalize_scroll_strategy(strategy: str) -> str:
    normalized = str(strategy or "").strip().lower()
    aliases = {
        "hybrid": DEFAULT_SCROLL_STRATEGY,
        "hybrid_wheel": DEFAULT_SCROLL_STRATEGY,
        "hybrid_wheel_pagedown": DEFAULT_SCROLL_STRATEGY,
        "pagedown_only": "pagedown_only",
        "pagedown": "pagedown_only",
        "page_down": "pagedown_only",
        "wheel_only": "wheel_only",
        "wheel": "wheel_only",
    }
    resolved = aliases.get(normalized, DEFAULT_SCROLL_STRATEGY)
    if resolved not in SCROLL_STRATEGIES:
        return DEFAULT_SCROLL_STRATEGY
    return resolved


def _normalize_wheel_injection_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized == "legacy_message_wheel":
        return "legacy_message_wheel"
    return DEFAULT_WHEEL_INJECTION_MODE


def _normalize_center_click_assist(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized == "off":
        return "off"
    return DEFAULT_CENTER_CLICK_ASSIST


def _normalize_cursor_hold_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized == "restore_each_step":
        return "restore_each_step"
    return DEFAULT_CURSOR_HOLD_MODE
