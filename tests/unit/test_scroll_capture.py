from __future__ import annotations

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
        self.click_calls: list[str] = []
        self.pagedown_calls = 0
        self.wait_calls: list[int] = []
        self.started_sessions: list[tuple[int, str]] = []
        self.ended_sessions = 0

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
    ) -> None:
        self.started_sessions.append((target_hwnd, cursor_hold_mode))

    def end_full_capture_input_session(self) -> None:
        self.ended_sessions += 1

    def capture_window(
        self,
        _hwnd: int,
        *,
        primary_backend: str = "screen_region_gdi",
    ) -> tuple[_FakePixmap | None, str]:
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
            scroll_strategy="hybrid_wheel_pagedown",
            center_click_assist="on_no_movement",
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
            center_click_assist="on_no_movement",
        ),
        stop_requested=lambda: False,
        progress_callback=progress.append,
    )

    assert result.captured_frames == 1
    assert result.stop_reason == "repeat_detected"
    assert service.pagedown_calls == 1
    assert progress[-1].stop_reason == "repeat_detected"
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
    assert service.started_sessions == [(4242, "restore_each_step")]
    assert service.ended_sessions == 1
    assert service.wheel_calls == []
    assert service.click_calls == []
    assert service.pagedown_calls == 0
