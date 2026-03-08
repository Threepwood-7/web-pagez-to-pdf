"""Browser-window auto-scroll capture orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageQt

from .stitching import frame_diff_score, stitch_frames

if TYPE_CHECKING:
    from collections.abc import Callable

    from .capture_service import WindowCaptureService


@dataclass(slots=True)
class ScrollCaptureOptions:
    """Configuration for full-page browser auto-scroll capture."""

    delay_ms: int = 380
    max_capture_pages: int = 18
    repeated_frame_score_threshold: float = 1.8
    repeated_frame_stop_count: int = 2


@dataclass(slots=True)
class ScrollCaptureResult:
    """Result payload from full-page capture."""

    image: Image.Image
    captured_frames: int
    ended_by_repeat: bool


def run_full_page_capture(
    service: WindowCaptureService,
    target_hwnd: int,
    options: ScrollCaptureOptions,
    stop_requested: Callable[[], bool],
) -> ScrollCaptureResult:
    """Capture browser frames while paging down, then stitch."""

    activated, reason = service.activate_window(target_hwnd)
    if not activated:
        raise RuntimeError(reason or "Could not bring selected window to foreground.")

    frames: list[Image.Image] = []
    repeated_count = 0
    ended_by_repeat = False

    for _index in range(max(1, options.max_capture_pages)):
        if stop_requested():
            break

        pixmap = service.capture_window(target_hwnd)
        if pixmap is None:
            break
        frame = ImageQt.fromqpixmap(pixmap).convert("RGB")

        if frames:
            diff_score = frame_diff_score(frames[-1], frame)
            if diff_score <= max(0.1, float(options.repeated_frame_score_threshold)):
                repeated_count += 1
                if repeated_count >= max(1, options.repeated_frame_stop_count):
                    ended_by_repeat = True
                    break
            else:
                repeated_count = 0

        frames.append(frame)
        if len(frames) >= max(1, options.max_capture_pages):
            break

        service.send_page_down()
        service.wait_after_scroll(max(120, options.delay_ms))

    if not frames:
        raise RuntimeError("No frames were captured.")

    stitched = stitch_frames(frames).image
    return ScrollCaptureResult(
        image=stitched,
        captured_frames=len(frames),
        ended_by_repeat=ended_by_repeat,
    )
