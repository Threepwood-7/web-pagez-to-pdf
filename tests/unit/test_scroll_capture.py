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
