from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace

from PIL import Image

import web_pagez_to_pdf.scroll_capture as scroll_capture


@dataclass
class _FakePixmap:
    image: Image.Image


class _FakeService:
    def __init__(
        self,
        captures: list[tuple[Image.Image, str]],
        *,
        wheel_success: list[bool] | None = None,
        click_success: bool = True,
    ) -> None:
        self._captures = captures[:]
        self._wheel_success = wheel_success[:] if wheel_success is not None else []
        self._click_success = click_success
        self.wheel_calls: list[tuple[str, str]] = []
        self.wheel_up_calls: list[tuple[str, str]] = []
        self.click_calls: list[str] = []
        self.pagedown_calls = 0
        self.home_calls = 0
        self.wait_calls: list[int] = []
        self.started_sessions: list[dict[str, str | int]] = []
        self.ended_sessions = 0
        self.capture_calls: list[tuple[str, bool]] = []

    @staticmethod
    def activate_window(_hwnd: int) -> tuple[bool, str]:
        return (True, "")

    @staticmethod
    def ensure_window_foreground(_hwnd: int) -> tuple[bool, str]:
        return (True, "")

    def start_full_capture_input_session(
        self,
        target_hwnd: int,
        *,
        cursor_hold_mode: str = "keep_at_center",
        session_id: str = "",
        target_label: str = "",
        target_process: str = "",
        scroll_strategy: str = "",
        capture_backend: str = "",
        wheel_injection_mode: str = "",
        center_click_assist: str = "",
    ) -> None:
        self.started_sessions.append(
            {
                "target_hwnd": target_hwnd,
                "cursor_hold_mode": cursor_hold_mode,
                "session_id": session_id,
                "target_label": target_label,
                "target_process": target_process,
                "scroll_strategy": scroll_strategy,
                "capture_backend": capture_backend,
                "wheel_injection_mode": wheel_injection_mode,
                "center_click_assist": center_click_assist,
            }
        )

    def end_full_capture_input_session(self) -> None:
        self.ended_sessions += 1

    @staticmethod
    def window_title(hwnd: int) -> str:
        return f"title-{hwnd}"

    @staticmethod
    def window_process_name(_hwnd: int) -> str:
        return "browser.exe"

    def capture_window(
        self,
        _hwnd: int,
        *,
        primary_backend: str = "screen_region_gdi",
        frame_region: str = "client_area",
        include_mouse_cursor: bool = False,
    ) -> tuple[_FakePixmap | None, str]:
        self.capture_calls.append((frame_region, bool(include_mouse_cursor)))
        if not self._captures:
            return (None, primary_backend)
        image, backend = self._captures.pop(0)
        return (_FakePixmap(image), backend)

    def wheel_down_at_window_center(
        self,
        _hwnd: int,
        *,
        wheel_injection_mode: str = "physical_center_sendinput",
        cursor_hold_mode: str = "keep_at_center",
    ) -> bool:
        self.wheel_calls.append((wheel_injection_mode, cursor_hold_mode))
        if self._wheel_success:
            return bool(self._wheel_success.pop(0))
        return True

    def wheel_up_at_window_center(
        self,
        _hwnd: int,
        *,
        wheel_injection_mode: str = "physical_center_sendinput",
        cursor_hold_mode: str = "keep_at_center",
    ) -> bool:
        self.wheel_up_calls.append((wheel_injection_mode, cursor_hold_mode))
        return True

    def click_window_center(
        self,
        _hwnd: int,
        *,
        cursor_hold_mode: str = "keep_at_center",
    ) -> bool:
        self.click_calls.append(cursor_hold_mode)
        return self._click_success

    def send_page_down(self) -> None:
        self.pagedown_calls += 1

    def send_home(self) -> bool:
        self.home_calls += 1
        return True

    def wait_after_scroll(self, delay_ms: int) -> None:
        self.wait_calls.append(delay_ms)


def _monkeypatch_image_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(scroll_capture.ImageQt, "fromqpixmap", lambda pixmap: pixmap.image)
    monkeypatch.setattr(
        scroll_capture,
        "frame_diff_score",
        lambda left, right: 0.0 if left.getpixel((0, 0)) == right.getpixel((0, 0)) else 10.0,
    )
    monkeypatch.setattr(
        scroll_capture,
        "stitch_frames",
        lambda frames: SimpleNamespace(image=frames[-1]),
    )


def test_run_full_capture_hybrid_uses_wheel_click_then_pagedown(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    img_b = Image.new("RGB", (8, 8), "blue")
    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),  # initial
            (img_a, "screen_region_gdi"),  # after wheel
            (img_a, "screen_region_gdi"),  # after click+wheel
            (img_b, "screen_region_gdi"),  # after pagedown
        ],
    )
    progress: list[scroll_capture.ScrollCaptureProgress] = []
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=2,
            delay_ms=160,
            scroll_mode="wheel_click_pagedown",
            wheel_injection_mode="physical_center_sendinput",
        ),
        stop_requested=lambda: False,
        progress_callback=progress.append,
    )

    assert result.captured_frames == 2
    assert result.stop_reason == "max_pages"
    assert len(service.wheel_calls) == 2
    assert len(service.click_calls) == 1
    assert service.pagedown_calls == 1
    assert progress[-1].scroll_method == "pagedown"


def test_run_full_capture_reports_repeat_after_full_ladder(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),  # initial
            (img_a, "screen_region_gdi"),  # after wheel
            (img_a, "screen_region_gdi"),  # after click+wheel
            (img_a, "screen_region_gdi"),  # after pagedown
        ],
    )
    progress: list[scroll_capture.ScrollCaptureProgress] = []
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=5,
            repeated_frame_stop_count=1,
            scroll_mode="wheel_click_pagedown",
        ),
        stop_requested=lambda: False,
        progress_callback=progress.append,
    )

    assert result.captured_frames == 1
    assert result.stop_reason == "capture_failed"
    assert service.pagedown_calls == 1
    assert progress[-1].stop_reason == "capture_failed"
    assert progress[-1].scroll_method == "pagedown"


def test_run_full_capture_starts_and_ends_cursor_session(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    service = _FakeService(captures=[(img_a, "screen_region_gdi")])

    calls = {"count": 0}

    def _stop_requested() -> bool:
        calls["count"] += 1
        return calls["count"] >= 1

    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=5,
            cursor_hold_mode="restore_each_step",
        ),
        stop_requested=_stop_requested,
    )

    assert result.captured_frames == 1
    assert result.stop_reason == "user_stop"
    assert len(service.started_sessions) == 1
    assert service.started_sessions[0]["target_hwnd"] == 4242
    assert service.started_sessions[0]["cursor_hold_mode"] == "restore_each_step"
    assert str(service.started_sessions[0]["session_id"]) != ""
    assert service.ended_sessions == 1
    assert service.wheel_calls == []
    assert service.click_calls == []
    assert service.pagedown_calls == 0


def test_run_full_capture_logs_movement_probe_verdicts(monkeypatch, caplog) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    img_b = Image.new("RGB", (8, 8), "blue")
    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),
            (img_a, "screen_region_gdi"),
            (img_a, "screen_region_gdi"),
            (img_b, "screen_region_gdi"),
        ],
    )
    caplog.set_level(logging.DEBUG, logger=scroll_capture.CAPTURE_LOGGER_NAME)
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=2,
            scroll_mode="wheel_click_pagedown",
        ),
        stop_requested=lambda: False,
    )

    assert result.stop_reason == "max_pages"
    assert any(
        "fallback=click_center_then_wheel" in record.message for record in caplog.records
    )
    assert any(
        "movement probe verdict=moved" in record.message for record in caplog.records
    )


def test_capture_logger_info_suppresses_debug(monkeypatch, caplog) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    logger = logging.getLogger(scroll_capture.CAPTURE_LOGGER_NAME)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        img = Image.new("RGB", (8, 8), "red")
        service = _FakeService(captures=[(img, "screen_region_gdi")])
        calls = {"count": 0}

        def _stop_requested() -> bool:
            calls["count"] += 1
            return calls["count"] >= 1

        caplog.set_level(logging.DEBUG, logger=scroll_capture.CAPTURE_LOGGER_NAME)
        scroll_capture.run_full_page_capture(
            service=service,
            target_hwnd=4242,
            options=scroll_capture.ScrollCaptureOptions(max_capture_pages=5),
            stop_requested=_stop_requested,
        )
    finally:
        logger.setLevel(old_level)

    assert any(record.levelno == logging.INFO for record in caplog.records)
    assert not any(record.levelno == logging.DEBUG for record in caplog.records)


def test_scroll_mode_wheel_only_never_uses_click_or_pagedown(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),
            (img_a, "screen_region_gdi"),
        ],
    )

    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=3,
            repeated_frame_stop_count=1,
            scroll_mode="wheel_only",
        ),
        stop_requested=lambda: False,
    )

    assert result.stop_reason == "repeat_detected"
    assert len(service.click_calls) == 0
    assert service.pagedown_calls == 0


def test_scroll_mode_wheel_pagedown_disables_click(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),
            (img_a, "screen_region_gdi"),
            (img_a, "screen_region_gdi"),
        ],
    )

    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=4,
            repeated_frame_stop_count=1,
            scroll_mode="wheel_pagedown",
        ),
        stop_requested=lambda: False,
    )

    assert result.stop_reason == "capture_failed"
    assert len(service.click_calls) == 0
    assert service.pagedown_calls == 1


def test_scroll_mode_wheel_then_pagedown_always_runs_both(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    img_b = Image.new("RGB", (8, 8), "blue")
    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),
            (img_b, "screen_region_gdi"),
        ],
    )

    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=2,
            scroll_mode="wheel_then_pagedown",
        ),
        stop_requested=lambda: False,
    )

    assert result.stop_reason == "max_pages"
    assert len(service.wheel_calls) == 1
    assert service.pagedown_calls == 1
    assert len(service.click_calls) == 0


def test_frame_region_and_cursor_options_passed_to_capture_window(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    service = _FakeService(captures=[(img_a, "screen_region_gdi")])

    calls = {"count": 0}

    def _stop_requested() -> bool:
        calls["count"] += 1
        return calls["count"] >= 1

    scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            frame_region="full_window",
            include_mouse_cursor=True,
        ),
        stop_requested=_stop_requested,
    )

    assert service.capture_calls
    assert service.capture_calls[0] == ("full_window", True)


def test_scroll_to_top_preflight_runs_when_enabled(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img = Image.new("RGB", (8, 8), "red")
    service = _FakeService(captures=[(img, "screen_region_gdi")])

    calls = {"count": 0}

    def _stop_requested() -> bool:
        calls["count"] += 1
        return calls["count"] >= 1

    scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=5,
            scroll_to_top_on_full=True,
        ),
        stop_requested=_stop_requested,
    )

    assert len(service.wheel_up_calls) == 1
    assert service.home_calls == 1


def test_scroll_to_top_preflight_skipped_when_disabled(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img = Image.new("RGB", (8, 8), "red")
    service = _FakeService(captures=[(img, "screen_region_gdi")])

    calls = {"count": 0}

    def _stop_requested() -> bool:
        calls["count"] += 1
        return calls["count"] >= 1

    scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=5,
            scroll_to_top_on_full=False,
        ),
        stop_requested=_stop_requested,
    )

    assert len(service.wheel_up_calls) == 0
    assert service.home_calls == 0


def test_estimate_fixed_vertical_strips_detects_top_and_bottom() -> None:
    width = 140
    height = 240
    fixed_top = 48
    fixed_bottom = 36
    img_a = Image.new("RGB", (width, height), "white")
    img_b = Image.new("RGB", (width, height), "white")
    for y_pos in range(fixed_top):
        for x_pos in range(width):
            img_a.putpixel((x_pos, y_pos), (22, 44, 66))
            img_b.putpixel((x_pos, y_pos), (22, 44, 66))
    for y_pos in range(height - fixed_bottom, height):
        for x_pos in range(width):
            img_a.putpixel((x_pos, y_pos), (80, 80, 80))
            img_b.putpixel((x_pos, y_pos), (80, 80, 80))
    for y_pos in range(fixed_top, height - fixed_bottom):
        for x_pos in range(width):
            if (y_pos + x_pos) % 19 == 0:
                img_a.putpixel((x_pos, y_pos), (6, 6, 6))
            if (y_pos + x_pos + 7) % 19 == 0:
                img_b.putpixel((x_pos, y_pos), (6, 6, 6))

    top_trim, bottom_trim = scroll_capture._estimate_fixed_vertical_strips(img_a, img_b)
    assert top_trim >= 24
    assert bottom_trim >= 24


def test_auto_trim_removes_fixed_top_and_bottom_strips_from_output_frames(monkeypatch) -> None:
    monkeypatch.setattr(scroll_capture.ImageQt, "fromqpixmap", lambda pixmap: pixmap.image)

    def _diff_score(left: Image.Image, right: Image.Image) -> float:
        width = min(left.width, right.width)
        height = min(left.height, right.height)
        left_px = left.load()
        right_px = right.load()
        acc = 0
        count = 0
        for y_pos in range(height):
            for x_pos in range(width):
                lv = left_px[x_pos, y_pos]
                rv = right_px[x_pos, y_pos]
                acc += abs(lv[0] - rv[0]) + abs(lv[1] - rv[1]) + abs(lv[2] - rv[2])
                count += 3
        return float(acc) / float(max(1, count))

    monkeypatch.setattr(scroll_capture, "frame_diff_score", _diff_score)
    captured_heights: list[int] = []

    def _stitch_frames(frames: list[Image.Image]):
        captured_heights.extend(frame.height for frame in frames)
        return SimpleNamespace(image=frames[-1])

    monkeypatch.setattr(scroll_capture, "stitch_frames", _stitch_frames)

    width = 120
    height = 180
    fixed_top = 52
    fixed_bottom = 34
    img_a = Image.new("RGB", (width, height), "white")
    img_b = Image.new("RGB", (width, height), "white")
    for y_pos in range(fixed_top):
        for x_pos in range(width):
            img_a.putpixel((x_pos, y_pos), (87, 110, 161))
            img_b.putpixel((x_pos, y_pos), (87, 110, 161))
    for y_pos in range(height - fixed_bottom, height):
        for x_pos in range(width):
            img_a.putpixel((x_pos, y_pos), (26, 26, 26))
            img_b.putpixel((x_pos, y_pos), (26, 26, 26))
    for y_pos in range(fixed_top, height - fixed_bottom):
        for x_pos in range(width):
            if (y_pos + x_pos) % 17 == 0:
                img_a.putpixel((x_pos, y_pos), (5, 5, 5))
            if (y_pos + x_pos + 6) % 17 == 0:
                img_b.putpixel((x_pos, y_pos), (5, 5, 5))

    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),
            (img_b, "screen_region_gdi"),
        ],
    )
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=2,
            scroll_mode="wheel_then_pagedown",
            frame_region="client_area",
        ),
        stop_requested=lambda: False,
    )

    assert result.stop_reason == "max_pages"
    assert captured_heights
    assert all(height_value <= (height - fixed_top - fixed_bottom) for height_value in captured_heights)


def test_auto_trim_keeps_movement_detection_with_fixed_top_and_bottom(monkeypatch) -> None:
    monkeypatch.setattr(scroll_capture.ImageQt, "fromqpixmap", lambda pixmap: pixmap.image)

    def _diff_score(left: Image.Image, right: Image.Image) -> float:
        width = min(left.width, right.width)
        height = min(left.height, right.height)
        left_px = left.load()
        right_px = right.load()
        acc = 0
        count = 0
        for y_pos in range(height):
            for x_pos in range(width):
                lv = left_px[x_pos, y_pos]
                rv = right_px[x_pos, y_pos]
                acc += abs(lv[0] - rv[0]) + abs(lv[1] - rv[1]) + abs(lv[2] - rv[2])
                count += 3
        return float(acc) / float(max(1, count))

    monkeypatch.setattr(scroll_capture, "frame_diff_score", _diff_score)
    monkeypatch.setattr(
        scroll_capture,
        "stitch_frames",
        lambda frames: SimpleNamespace(image=frames[-1]),
    )

    width = 180
    height = 220
    fixed_top = 60
    fixed_bottom = 60
    img_a = Image.new("RGB", (width, height), "white")
    img_b = Image.new("RGB", (width, height), "white")
    for y_pos in range(fixed_top):
        for x_pos in range(width):
            img_a.putpixel((x_pos, y_pos), (100, 100, 100))
            img_b.putpixel((x_pos, y_pos), (100, 100, 100))
    for y_pos in range(height - fixed_bottom, height):
        for x_pos in range(width):
            img_a.putpixel((x_pos, y_pos), (40, 40, 40))
            img_b.putpixel((x_pos, y_pos), (40, 40, 40))
    for y_pos in range(fixed_top, height - fixed_bottom):
        for x_pos in range(width):
            if (y_pos + x_pos) % 13 == 0:
                img_a.putpixel((x_pos, y_pos), (12, 12, 12))
            if (y_pos + x_pos + 4) % 13 == 0:
                img_b.putpixel((x_pos, y_pos), (12, 12, 12))

    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),
            (img_b, "screen_region_gdi"),
        ],
    )
    progress: list[scroll_capture.ScrollCaptureProgress] = []
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=2,
            scroll_mode="wheel_then_pagedown",
            frame_region="client_area",
            repeated_frame_score_threshold=6.0,
            repeated_frame_stop_count=1,
            auto_trim_fixed_strips=True,
        ),
        stop_requested=lambda: False,
        progress_callback=progress.append,
    )

    assert result.stop_reason == "max_pages"
    assert any("captured via" in payload.message.lower() for payload in progress)


def _make_right_scrollbar_frame(
    *,
    width: int,
    height: int,
    scrollbar_width: int,
    offset: int,
    page_base: tuple[int, int, int] = (255, 255, 255),
    page_noise: tuple[int, int, int] = (32, 32, 32),
    track_color: tuple[int, int, int] = (232, 232, 232),
    thumb_color: tuple[int, int, int] = (168, 168, 168),
    noise_mod: int = 13,
) -> Image.Image:
    image = Image.new("RGB", (width, height), page_base)
    for y_pos in range(height):
        for x_pos in range(width - scrollbar_width):
            if (x_pos + y_pos + offset) % max(3, noise_mod) == 0:
                image.putpixel((x_pos, y_pos), page_noise)
    for y_pos in range(height):
        for x_pos in range(width - scrollbar_width, width):
            image.putpixel((x_pos, y_pos), track_color)
    thumb_top = max(8, height // 4)
    thumb_bottom = min(height - 8, thumb_top + max(24, height // 5))
    for y_pos in range(thumb_top, thumb_bottom):
        for x_pos in range(width - scrollbar_width + 2, width - 2):
            image.putpixel((x_pos, y_pos), thumb_color)
    return image


def test_estimate_right_scrollbar_trim_from_pair_detects_stable_band() -> None:
    left = _make_right_scrollbar_frame(width=220, height=180, scrollbar_width=14, offset=0)
    right = _make_right_scrollbar_frame(width=220, height=180, scrollbar_width=14, offset=7)

    trim = scroll_capture._estimate_right_scrollbar_trim_from_pair(left, right)

    assert 10 <= trim <= 18


def test_estimate_right_scrollbar_trim_from_pair_returns_zero_without_left_movement() -> None:
    left = _make_right_scrollbar_frame(width=220, height=180, scrollbar_width=14, offset=0)
    right = _make_right_scrollbar_frame(width=220, height=180, scrollbar_width=14, offset=0)

    trim = scroll_capture._estimate_right_scrollbar_trim_from_pair(left, right)

    assert trim == 0


def test_detect_right_scrollbar_trim_single_frame_detects_band() -> None:
    frame = _make_right_scrollbar_frame(width=200, height=160, scrollbar_width=12, offset=4)

    trim = scroll_capture.detect_right_scrollbar_trim_single_frame(frame)

    assert 8 <= trim <= 16


def test_detect_right_scrollbar_trim_single_frame_detects_dark_theme_band() -> None:
    frame = _make_right_scrollbar_frame(
        width=220,
        height=170,
        scrollbar_width=10,
        offset=3,
        page_base=(34, 34, 34),
        page_noise=(86, 86, 86),
        track_color=(58, 58, 58),
        thumb_color=(118, 118, 118),
        noise_mod=11,
    )

    trim = scroll_capture.detect_right_scrollbar_trim_single_frame(frame)

    assert 6 <= trim <= 14


def test_detect_right_scrollbar_trim_single_frame_detects_low_contrast_band() -> None:
    frame = _make_right_scrollbar_frame(
        width=224,
        height=168,
        scrollbar_width=12,
        offset=5,
        page_base=(214, 214, 214),
        page_noise=(198, 198, 198),
        track_color=(206, 206, 206),
        thumb_color=(192, 192, 192),
        noise_mod=9,
    )

    trim = scroll_capture.detect_right_scrollbar_trim_single_frame(frame)

    assert 7 <= trim <= 16


def test_detect_right_scrollbar_trim_single_frame_returns_zero_for_uncertain_right_strip() -> None:
    frame = Image.new("RGB", (220, 160), (205, 205, 205))
    for y_pos in range(frame.height):
        for x_pos in range(frame.width):
            value = 205
            if (x_pos + y_pos) % 19 == 0:
                value = 200
            if x_pos > frame.width - 14:
                value = 206
            frame.putpixel((x_pos, y_pos), (value, value, value))

    trim = scroll_capture.detect_right_scrollbar_trim_single_frame(frame)

    assert trim == 0


def test_auto_trim_scrollbar_reduces_output_width_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(scroll_capture.ImageQt, "fromqpixmap", lambda pixmap: pixmap.image)
    monkeypatch.setattr(scroll_capture, "frame_diff_score", lambda _a, _b: 12.0)

    output_widths: list[int] = []

    def _stitch_frames(frames: list[Image.Image]):
        output_widths.extend(frame.width for frame in frames)
        return SimpleNamespace(image=frames[-1])

    monkeypatch.setattr(scroll_capture, "stitch_frames", _stitch_frames)

    service = _FakeService(
        captures=[
            (_make_right_scrollbar_frame(width=210, height=150, scrollbar_width=12, offset=0), "screen_region_gdi"),
            (_make_right_scrollbar_frame(width=210, height=150, scrollbar_width=12, offset=5), "screen_region_gdi"),
            (_make_right_scrollbar_frame(width=210, height=150, scrollbar_width=12, offset=10), "screen_region_gdi"),
        ],
    )
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=3,
            scroll_mode="wheel_then_pagedown",
            auto_trim_scrollbar=True,
            auto_trim_fixed_strips=False,
        ),
        stop_requested=lambda: False,
    )

    assert result.stop_reason == "max_pages"
    assert output_widths
    assert max(output_widths) < 210


def test_auto_trim_scrollbar_keeps_output_width_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(scroll_capture.ImageQt, "fromqpixmap", lambda pixmap: pixmap.image)
    monkeypatch.setattr(scroll_capture, "frame_diff_score", lambda _a, _b: 12.0)

    output_widths: list[int] = []

    def _stitch_frames(frames: list[Image.Image]):
        output_widths.extend(frame.width for frame in frames)
        return SimpleNamespace(image=frames[-1])

    monkeypatch.setattr(scroll_capture, "stitch_frames", _stitch_frames)

    service = _FakeService(
        captures=[
            (_make_right_scrollbar_frame(width=210, height=150, scrollbar_width=12, offset=0), "screen_region_gdi"),
            (_make_right_scrollbar_frame(width=210, height=150, scrollbar_width=12, offset=5), "screen_region_gdi"),
            (_make_right_scrollbar_frame(width=210, height=150, scrollbar_width=12, offset=10), "screen_region_gdi"),
        ],
    )
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=3,
            scroll_mode="wheel_then_pagedown",
            auto_trim_scrollbar=False,
            auto_trim_fixed_strips=False,
        ),
        stop_requested=lambda: False,
    )

    assert result.stop_reason == "max_pages"
    assert output_widths
    assert all(width_value == 210 for width_value in output_widths)
