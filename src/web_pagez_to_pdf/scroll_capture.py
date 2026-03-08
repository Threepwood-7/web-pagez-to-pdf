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

    first_pixmap, first_backend = service.capture_window(
        target_hwnd,
        primary_backend=options.capture_backend,
    )
    if first_pixmap is None:
        raise RuntimeError("No frames were captured.")

    frames: list[Image.Image] = [ImageQt.fromqpixmap(first_pixmap).convert("RGB")]
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

        scroll_method = service.scroll_target_window(target_hwnd, strategy=scroll_strategy)
        if not scroll_method:
            scroll_method = "fallback"
        service.wait_after_scroll(delay_ms)

        attempted_frame = len(frames) + 1
        pixmap, backend_used = service.capture_window(
            target_hwnd,
            primary_backend=options.capture_backend,
        )
        if pixmap is None:
            stop_reason = STOP_REASON_CAPTURE_FAILED
            _emit_progress(
                progress_callback,
                ScrollCaptureProgress(
                    frame_index=attempted_frame,
                    backend_used=backend_used,
                    scroll_method=scroll_method,
                    diff_score=None,
                    repeated_count=repeated_count,
                    stop_reason=stop_reason,
                    message=f"Frame {attempted_frame} capture failed after scroll={scroll_method}.",
                ),
            )
            break

        frame = ImageQt.fromqpixmap(pixmap).convert("RGB")
        diff_score = frame_diff_score(frames[-1], frame)
        if scroll_strategy == DEFAULT_SCROLL_STRATEGY and diff_score <= threshold:
            fallback_method = service.scroll_target_window(target_hwnd, strategy="pagedown_only")
            service.wait_after_scroll(delay_ms)
            fallback_pixmap, fallback_backend = service.capture_window(
                target_hwnd,
                primary_backend=options.capture_backend,
            )
            if fallback_pixmap is None:
                stop_reason = STOP_REASON_CAPTURE_FAILED
                _emit_progress(
                    progress_callback,
                    ScrollCaptureProgress(
                        frame_index=attempted_frame,
                        backend_used=fallback_backend,
                        scroll_method=fallback_method or "pagedown",
                        diff_score=diff_score,
                        repeated_count=repeated_count,
                        stop_reason=stop_reason,
                        message=f"Frame {attempted_frame} fallback capture failed after no wheel movement.",
                    ),
                )
                break
            frame = ImageQt.fromqpixmap(fallback_pixmap).convert("RGB")
            diff_score = frame_diff_score(frames[-1], frame)
            backend_used = fallback_backend or backend_used
            if diff_score <= threshold:
                scroll_method = "fallback"
            else:
                scroll_method = fallback_method or "pagedown"

        if diff_score <= threshold:
            repeated_count += 1
            message = (
                f"Frame {attempted_frame}: no movement (scroll={scroll_method}, "
                f"diff={diff_score:.2f}, repeat={repeated_count}/{repeat_stop})."
            )
            if repeated_count >= repeat_stop:
                stop_reason = STOP_REASON_REPEAT
                _emit_progress(
                    progress_callback,
                    ScrollCaptureProgress(
                        frame_index=attempted_frame,
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
                f"Frame {attempted_frame} captured via {backend_used or 'unknown backend'} "
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
