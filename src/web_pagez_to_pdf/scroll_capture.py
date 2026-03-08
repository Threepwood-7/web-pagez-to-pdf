"""Browser-window auto-scroll capture orchestration."""

from __future__ import annotations

import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageQt

from .capture_service import CAPTURE_FRAME_REGIONS, DEFAULT_CAPTURE_FRAME_REGION
from .stitching import frame_diff_score, stitch_frames

if TYPE_CHECKING:
    from collections.abc import Callable

    from .capture_service import WindowCaptureService

DEFAULT_SCROLL_MODE = "wheel_then_pagedown"
SCROLL_MODES = (
    DEFAULT_SCROLL_MODE,
    "wheel_only",
    "wheel_click",
    "wheel_pagedown",
    "wheel_click_pagedown",
)
DEFAULT_WHEEL_INJECTION_MODE = "physical_center_sendinput"
WHEEL_INJECTION_MODES = (
    DEFAULT_WHEEL_INJECTION_MODE,
    "legacy_message_wheel",
)
DEFAULT_CAPTURE_LOG_LEVEL = "DEBUG"
CAPTURE_LOG_LEVELS = (
    "INFO",
    DEFAULT_CAPTURE_LOG_LEVEL,
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
CAPTURE_LOGGER_NAME = "web_pagez_to_pdf.capture"
LOGGER = logging.getLogger(CAPTURE_LOGGER_NAME)


@dataclass(slots=True)
class ScrollCaptureOptions:
    """Configuration for full-page browser auto-scroll capture."""

    delay_ms: int = 380
    max_capture_pages: int = 18
    capture_backend: str = "screen_region_gdi"
    scroll_mode: str = DEFAULT_SCROLL_MODE
    wheel_injection_mode: str = DEFAULT_WHEEL_INJECTION_MODE
    cursor_hold_mode: str = DEFAULT_CURSOR_HOLD_MODE
    frame_region: str = DEFAULT_CAPTURE_FRAME_REGION
    include_mouse_cursor: bool = False
    scroll_to_top_on_full: bool = True
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


@dataclass(slots=True)
class _ScrollStepOutcome:
    """Internal per-step ladder result with movement verdict metadata."""

    frame: Image.Image | None
    backend_used: str
    scroll_method: str
    diff_score: float | None
    movement_detected: bool
    probe_exhausted: bool


def run_full_page_capture(
    service: WindowCaptureService,
    target_hwnd: int,
    options: ScrollCaptureOptions,
    stop_requested: Callable[[], bool],
    progress_callback: Callable[[ScrollCaptureProgress], None] | None = None,
) -> ScrollCaptureResult:
    """Capture browser frames while scrolling and then stitch."""

    session_id = uuid.uuid4().hex[:10]
    target_title = _service_window_title(service, target_hwnd)
    target_process = _service_window_process(service, target_hwnd)
    activated, reason = service.activate_window(target_hwnd)
    if not activated:
        LOGGER.error(
            "[capture-session:%s] preflight focus failed hwnd=%s title=%r reason=%s",
            session_id,
            target_hwnd,
            target_title,
            reason or "unknown",
        )
        raise RuntimeError(reason or "Could not bring selected window to foreground.")

    max_pages = max(1, int(options.max_capture_pages))
    threshold = max(0.1, float(options.repeated_frame_score_threshold))
    repeat_stop = max(1, int(options.repeated_frame_stop_count))
    delay_ms = max(120, int(options.delay_ms))
    scroll_mode = _normalize_scroll_mode(options.scroll_mode)
    wheel_mode = _normalize_wheel_injection_mode(options.wheel_injection_mode)
    cursor_hold_mode = _normalize_cursor_hold_mode(options.cursor_hold_mode)
    frame_region = _normalize_frame_region(options.frame_region)
    include_mouse_cursor = bool(options.include_mouse_cursor)
    scroll_to_top_on_full = bool(options.scroll_to_top_on_full)
    LOGGER.info(
        "[capture-session:%s] full-capture start hwnd=%s title=%r process=%r backend=%s "
        "scroll_mode=%s wheel=%s cursor_hold=%s frame_region=%s include_mouse_cursor=%s "
        "scroll_to_top=%s max_pages=%s delay_ms=%s",
        session_id,
        target_hwnd,
        target_title,
        target_process,
        options.capture_backend,
        scroll_mode,
        wheel_mode,
        cursor_hold_mode,
        frame_region,
        include_mouse_cursor,
        scroll_to_top_on_full,
        max_pages,
        delay_ms,
    )

    service.start_full_capture_input_session(
        target_hwnd,
        cursor_hold_mode=cursor_hold_mode,
        session_id=session_id,
        target_label=target_title,
        target_process=target_process,
        scroll_strategy=scroll_mode,
        capture_backend=options.capture_backend,
        wheel_injection_mode=wheel_mode,
        center_click_assist=scroll_mode,
    )
    try:
        if scroll_to_top_on_full:
            _run_scroll_to_top_preflight(
                service=service,
                target_hwnd=target_hwnd,
                session_id=session_id,
                wheel_mode=wheel_mode,
                cursor_hold_mode=cursor_hold_mode,
            )
        first_frame, first_backend = _capture_frame(
            service,
            target_hwnd,
            options.capture_backend,
            frame_region=frame_region,
            include_mouse_cursor=include_mouse_cursor,
        )
        if first_frame is None:
            LOGGER.error(
                "[capture-session:%s] initial frame capture failed backend=%s",
                session_id,
                options.capture_backend,
            )
            raise RuntimeError("No frames were captured.")

        frames: list[Image.Image] = [first_frame]
        repeated_count = 0
        stop_reason = STOP_REASON_RUNNING
        LOGGER.info(
            "[capture-session:%s] frame=1 backend=%s method=initial movement=accepted",
            session_id,
            first_backend or "unknown",
        )
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
                LOGGER.info(
                    "[capture-session:%s] stop requested by user at frame_count=%s",
                    session_id,
                    len(frames),
                )
                break

            focused, focus_reason = service.ensure_window_foreground(target_hwnd)
            LOGGER.debug(
                "[capture-session:%s] focus-check frame=%s focused=%s reason=%s",
                session_id,
                len(frames) + 1,
                focused,
                focus_reason or "",
            )
            if not focused:
                stop_reason = STOP_REASON_CAPTURE_FAILED
                LOGGER.error(
                    "[capture-session:%s] focus-check failed frame=%s reason=%s",
                    session_id,
                    len(frames) + 1,
                    focus_reason or "unknown",
                )
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
            outcome = _capture_after_scroll_ladder(
                service=service,
                target_hwnd=target_hwnd,
                previous_frame=frames[-1],
                frame_index=attempted_frame_index,
                session_id=session_id,
                delay_ms=delay_ms,
                capture_backend=options.capture_backend,
                scroll_mode=scroll_mode,
                wheel_mode=wheel_mode,
                cursor_hold_mode=cursor_hold_mode,
                frame_region=frame_region,
                include_mouse_cursor=include_mouse_cursor,
                threshold=threshold,
            )
            frame = outcome.frame
            if frame is None:
                stop_reason = STOP_REASON_CAPTURE_FAILED
                LOGGER.error(
                    "[capture-session:%s] frame=%s capture failed method=%s backend=%s",
                    session_id,
                    attempted_frame_index,
                    outcome.scroll_method,
                    outcome.backend_used or "unknown",
                )
                _emit_progress(
                    progress_callback,
                    ScrollCaptureProgress(
                        frame_index=attempted_frame_index,
                        backend_used=outcome.backend_used,
                        scroll_method=outcome.scroll_method,
                        diff_score=None,
                        repeated_count=repeated_count,
                        stop_reason=stop_reason,
                        message=(
                            f"Frame {attempted_frame_index} capture failed "
                            f"after scroll={outcome.scroll_method}."
                        ),
                    ),
                )
                break

            diff_score = outcome.diff_score
            diff_text = _diff_text(diff_score)
            if not outcome.movement_detected:
                repeated_count += 1
                if outcome.probe_exhausted:
                    stop_reason = STOP_REASON_CAPTURE_FAILED
                    message = (
                        f"Frame {attempted_frame_index}: movement probe failed "
                        f"(scroll={outcome.scroll_method}, diff={diff_text})."
                    )
                    LOGGER.error(
                        "[capture-session:%s] movement probe verdict=stalled frame=%s "
                        "method=%s diff=%s backend=%s action=stop_capture_failed",
                        session_id,
                        attempted_frame_index,
                        outcome.scroll_method,
                        diff_text,
                        outcome.backend_used or "unknown",
                    )
                    _emit_progress(
                        progress_callback,
                        ScrollCaptureProgress(
                            frame_index=attempted_frame_index,
                            backend_used=outcome.backend_used,
                            scroll_method=outcome.scroll_method,
                            diff_score=diff_score,
                            repeated_count=repeated_count,
                            stop_reason=STOP_REASON_CAPTURE_FAILED,
                            message=message,
                        ),
                    )
                    break
                message = (
                    f"Frame {attempted_frame_index}: no movement (scroll={outcome.scroll_method}, "
                    f"diff={diff_text}, repeat={repeated_count}/{repeat_stop})."
                )
                LOGGER.info(
                    "[capture-session:%s] movement probe verdict=stalled frame=%s method=%s "
                    "diff=%s repeat=%s/%s",
                    session_id,
                    attempted_frame_index,
                    outcome.scroll_method,
                    diff_text,
                    repeated_count,
                    repeat_stop,
                )
                if repeated_count >= repeat_stop:
                    stop_reason = STOP_REASON_REPEAT
                    _emit_progress(
                        progress_callback,
                        ScrollCaptureProgress(
                            frame_index=attempted_frame_index,
                            backend_used=outcome.backend_used,
                            scroll_method=outcome.scroll_method,
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
                    f"Frame {attempted_frame_index} captured via "
                    f"{outcome.backend_used or 'unknown backend'} "
                    f"(scroll={outcome.scroll_method}, diff={diff_text})."
                )
                LOGGER.info(
                    "[capture-session:%s] movement probe verdict=moved frame=%s method=%s "
                    "diff=%s backend=%s",
                    session_id,
                    attempted_frame_index,
                    outcome.scroll_method,
                    diff_text,
                    outcome.backend_used or "unknown",
                )

            frames.append(frame)
            _emit_progress(
                progress_callback,
                ScrollCaptureProgress(
                    frame_index=len(frames),
                    backend_used=outcome.backend_used,
                    scroll_method=outcome.scroll_method,
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
        LOGGER.info(
            "[capture-session:%s] full-capture complete stop_reason=%s frames=%s",
            session_id,
            stop_reason,
            len(frames),
        )
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
    frame_index: int,
    session_id: str,
    delay_ms: int,
    capture_backend: str,
    scroll_mode: str,
    wheel_mode: str,
    cursor_hold_mode: str,
    frame_region: str,
    include_mouse_cursor: bool,
    threshold: float,
) -> _ScrollStepOutcome:
    if scroll_mode == "wheel_then_pagedown":
        wheel_ok = service.wheel_down_at_window_center(
            target_hwnd,
            wheel_injection_mode=wheel_mode,
            cursor_hold_mode=cursor_hold_mode,
        )
        LOGGER.debug(
            "[capture-session:%s] frame=%s stage=wheel_center_pre_pagedown wheel_ok=%s",
            session_id,
            frame_index,
            wheel_ok,
        )
        service.send_page_down()
        frame_after_page, backend_after_page, diff_after_page = _capture_frame_with_diff(
            service=service,
            target_hwnd=target_hwnd,
            previous_frame=previous_frame,
            capture_backend=capture_backend,
            delay_ms=delay_ms,
            frame_region=frame_region,
            include_mouse_cursor=include_mouse_cursor,
        )
        LOGGER.debug(
            "[capture-session:%s] frame=%s stage=wheel_then_pagedown diff=%s backend=%s",
            session_id,
            frame_index,
            _diff_text(diff_after_page),
            backend_after_page or "unknown",
        )
        if frame_after_page is None:
            return _ScrollStepOutcome(
                frame=None,
                backend_used=backend_after_page,
                scroll_method="wheel_then_pagedown",
                diff_score=None,
                movement_detected=False,
                probe_exhausted=False,
            )
        moved = bool(diff_after_page is not None and diff_after_page > threshold)
        return _ScrollStepOutcome(
            frame=frame_after_page,
            backend_used=backend_after_page,
            scroll_method="wheel_then_pagedown",
            diff_score=diff_after_page,
            movement_detected=moved,
            probe_exhausted=False,
        )

    wheel_ok = service.wheel_down_at_window_center(
        target_hwnd,
        wheel_injection_mode=wheel_mode,
        cursor_hold_mode=cursor_hold_mode,
    )
    frame, backend, diff_score = _capture_frame_with_diff(
        service=service,
        target_hwnd=target_hwnd,
        previous_frame=previous_frame,
        capture_backend=capture_backend,
        delay_ms=delay_ms,
        frame_region=frame_region,
        include_mouse_cursor=include_mouse_cursor,
    )
    LOGGER.debug(
        "[capture-session:%s] frame=%s stage=wheel_center wheel_ok=%s diff=%s backend=%s",
        session_id,
        frame_index,
        wheel_ok,
        _diff_text(diff_score),
        backend or "unknown",
    )
    if frame is None:
        return _ScrollStepOutcome(
            frame=None,
            backend_used=backend,
            scroll_method="wheel_center",
            diff_score=None,
            movement_detected=False,
            probe_exhausted=scroll_mode in {"wheel_pagedown", "wheel_click_pagedown"},
        )
    if diff_score is not None and diff_score > threshold:
        return _ScrollStepOutcome(
            frame=frame,
            backend_used=backend,
            scroll_method="wheel_center",
            diff_score=diff_score,
            movement_detected=True,
            probe_exhausted=False,
        )

    if scroll_mode in {"wheel_click", "wheel_click_pagedown"}:
        LOGGER.debug(
            "[capture-session:%s] frame=%s fallback=click_center_then_wheel reason=no_movement "
            "diff=%s",
            session_id,
            frame_index,
            _diff_text(diff_score),
        )
        click_ok = service.click_window_center(target_hwnd, cursor_hold_mode=cursor_hold_mode)
        wheel_after_click_ok = service.wheel_down_at_window_center(
            target_hwnd,
            wheel_injection_mode=wheel_mode,
            cursor_hold_mode=cursor_hold_mode,
        )
        frame_after_click, backend_after_click, diff_after_click = _capture_frame_with_diff(
            service=service,
            target_hwnd=target_hwnd,
            previous_frame=previous_frame,
            capture_backend=capture_backend,
            delay_ms=delay_ms,
            frame_region=frame_region,
            include_mouse_cursor=include_mouse_cursor,
        )
        LOGGER.debug(
            "[capture-session:%s] frame=%s stage=click_center_then_wheel click_ok=%s "
            "wheel_ok=%s diff=%s backend=%s",
            session_id,
            frame_index,
            click_ok,
            wheel_after_click_ok,
            _diff_text(diff_after_click),
            backend_after_click or backend or "unknown",
        )
        if frame_after_click is None:
            return _ScrollStepOutcome(
                frame=None,
                backend_used=backend_after_click or backend,
                scroll_method="click_center_then_wheel",
                diff_score=None,
                movement_detected=False,
                probe_exhausted=scroll_mode == "wheel_click_pagedown",
            )
        frame = frame_after_click
        backend = backend_after_click or backend
        diff_score = diff_after_click
        if diff_score is not None and diff_score > threshold:
            return _ScrollStepOutcome(
                frame=frame,
                backend_used=backend,
                scroll_method="click_center_then_wheel",
                diff_score=diff_score,
                movement_detected=True,
                probe_exhausted=False,
            )
        if scroll_mode == "wheel_click":
            return _ScrollStepOutcome(
                frame=frame,
                backend_used=backend,
                scroll_method="click_center_then_wheel",
                diff_score=diff_score,
                movement_detected=False,
                probe_exhausted=False,
            )
    elif scroll_mode == "wheel_only":
        return _ScrollStepOutcome(
            frame=frame,
            backend_used=backend,
            scroll_method="wheel_center",
            diff_score=diff_score,
            movement_detected=False,
            probe_exhausted=False,
        )

    if scroll_mode in {"wheel_pagedown", "wheel_click_pagedown"}:
        LOGGER.debug(
            "[capture-session:%s] frame=%s fallback=pagedown reason=no_movement diff=%s",
            session_id,
            frame_index,
            _diff_text(diff_score),
        )
        service.send_page_down()
        frame_after_page, backend_after_page, diff_after_page = _capture_frame_with_diff(
            service=service,
            target_hwnd=target_hwnd,
            previous_frame=previous_frame,
            capture_backend=capture_backend,
            delay_ms=delay_ms,
            frame_region=frame_region,
            include_mouse_cursor=include_mouse_cursor,
        )
        LOGGER.debug(
            "[capture-session:%s] frame=%s stage=pagedown diff=%s backend=%s",
            session_id,
            frame_index,
            _diff_text(diff_after_page),
            backend_after_page or backend or "unknown",
        )
        if frame_after_page is None:
            return _ScrollStepOutcome(
                frame=None,
                backend_used=backend_after_page or backend,
                scroll_method="pagedown",
                diff_score=None,
                movement_detected=False,
                probe_exhausted=True,
            )
        final_backend = backend_after_page or backend
        moved = bool(diff_after_page is not None and diff_after_page > threshold)
        return _ScrollStepOutcome(
            frame=frame_after_page,
            backend_used=final_backend,
            scroll_method="pagedown",
            diff_score=diff_after_page,
            movement_detected=moved,
            probe_exhausted=not moved,
        )

    return _ScrollStepOutcome(
        frame=frame,
        backend_used=backend,
        scroll_method="wheel_center",
        diff_score=diff_score,
        movement_detected=False,
        probe_exhausted=scroll_mode in {"wheel_pagedown", "wheel_click_pagedown"},
    )


def _run_scroll_to_top_preflight(
    *,
    service: WindowCaptureService,
    target_hwnd: int,
    session_id: str,
    wheel_mode: str,
    cursor_hold_mode: str,
) -> None:
    focused, reason = service.ensure_window_foreground(target_hwnd)
    LOGGER.info(
        "[capture-session:%s] scroll-to-top preflight focus_ok=%s reason=%s",
        session_id,
        focused,
        reason or "",
    )
    if not focused:
        LOGGER.warning(
            "[capture-session:%s] scroll-to-top preflight skipped due to focus failure",
            session_id,
        )
        return
    wheel_ok = service.wheel_up_at_window_center(
        target_hwnd,
        wheel_injection_mode=wheel_mode,
        cursor_hold_mode=cursor_hold_mode,
    )
    home_sent = service.send_home()
    LOGGER.info(
        "[capture-session:%s] scroll-to-top preflight wheel_up_ok=%s home_sent=%s",
        session_id,
        wheel_ok,
        home_sent,
    )
    if not wheel_ok or not home_sent:
        LOGGER.warning(
            "[capture-session:%s] scroll-to-top preflight partial failure (non-fatal)",
            session_id,
        )


def _capture_frame(
    service: WindowCaptureService,
    target_hwnd: int,
    capture_backend: str,
    *,
    frame_region: str,
    include_mouse_cursor: bool,
) -> tuple[Image.Image | None, str]:
    pixmap, backend_used = service.capture_window(
        target_hwnd,
        primary_backend=capture_backend,
        frame_region=frame_region,
        include_mouse_cursor=include_mouse_cursor,
    )
    if pixmap is None:
        return (None, backend_used)
    return (ImageQt.fromqpixmap(pixmap).convert("RGB"), backend_used)


def _capture_frame_with_diff(
    *,
    service: WindowCaptureService,
    target_hwnd: int,
    previous_frame: Image.Image,
    capture_backend: str,
    delay_ms: int,
    frame_region: str,
    include_mouse_cursor: bool,
) -> tuple[Image.Image | None, str, float | None]:
    service.wait_after_scroll(delay_ms)
    frame, backend = _capture_frame(
        service,
        target_hwnd,
        capture_backend,
        frame_region=frame_region,
        include_mouse_cursor=include_mouse_cursor,
    )
    if frame is None:
        return (None, backend, None)
    return (frame, backend, frame_diff_score(previous_frame, frame))


def _emit_progress(
    callback: Callable[[ScrollCaptureProgress], None] | None,
    payload: ScrollCaptureProgress,
) -> None:
    if callback is None:
        return
    callback(payload)


def _normalize_scroll_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized not in SCROLL_MODES:
        return DEFAULT_SCROLL_MODE
    return normalized


def _normalize_wheel_injection_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized == "legacy_message_wheel":
        return "legacy_message_wheel"
    return DEFAULT_WHEEL_INJECTION_MODE


def _normalize_cursor_hold_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized == "restore_each_step":
        return "restore_each_step"
    return DEFAULT_CURSOR_HOLD_MODE


def _normalize_frame_region(frame_region: str) -> str:
    normalized = str(frame_region or "").strip().lower()
    if normalized not in CAPTURE_FRAME_REGIONS:
        return DEFAULT_CAPTURE_FRAME_REGION
    return normalized


def normalize_capture_log_level(level: str) -> str:
    """Normalize capture diagnostics level to one supported value."""

    normalized = str(level or "").strip().upper()
    if normalized == "INFO":
        return "INFO"
    return DEFAULT_CAPTURE_LOG_LEVEL


def _service_window_title(service: WindowCaptureService, hwnd: int) -> str:
    title_getter = getattr(service, "window_title", None)
    if callable(title_getter):
        with suppress(Exception):
            return str(title_getter(hwnd) or f"hwnd:{hwnd}")
    return f"hwnd:{hwnd}"


def _service_window_process(service: WindowCaptureService, hwnd: int) -> str:
    process_getter = getattr(service, "window_process_name", None)
    if callable(process_getter):
        with suppress(Exception):
            return str(process_getter(hwnd) or "")
    return ""


def _diff_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"
