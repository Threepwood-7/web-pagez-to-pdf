from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from PIL import Image

import web_pagez_to_pdf.scroll_capture as scroll_capture


@dataclass
class _FakePixmap:
    image: Image.Image


class _FakeService:
    def __init__(self, captures: list[tuple[Image.Image, str]]) -> None:
        self._captures = captures[:]
        self.scroll_calls: list[str] = []
        self.wait_calls: list[int] = []

    @staticmethod
    def activate_window(_hwnd: int) -> tuple[bool, str]:
        return (True, "")

    @staticmethod
    def ensure_window_foreground(_hwnd: int) -> tuple[bool, str]:
        return (True, "")

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

    def scroll_target_window(self, _hwnd: int, strategy: str = "hybrid_wheel_pagedown") -> str:
        self.scroll_calls.append(strategy)
        if strategy == "pagedown_only":
            return "pagedown"
        if strategy == "wheel_only":
            return "wheel"
        return "wheel"

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


def test_run_full_capture_hybrid_falls_back_to_pagedown(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    img_b = Image.new("RGB", (8, 8), "blue")
    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),
            (img_a, "screen_region_gdi"),
            (img_b, "screen_region_gdi"),
        ]
    )
    progress: list[scroll_capture.ScrollCaptureProgress] = []
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=2,
            delay_ms=160,
            scroll_strategy="hybrid_wheel_pagedown",
        ),
        stop_requested=lambda: False,
        progress_callback=progress.append,
    )

    assert result.captured_frames == 2
    assert result.stop_reason == "max_pages"
    assert service.scroll_calls == ["hybrid_wheel_pagedown", "pagedown_only"]
    assert progress[-1].scroll_method == "pagedown"


def test_run_full_capture_reports_repeat_detected(monkeypatch) -> None:
    _monkeypatch_image_pipeline(monkeypatch)
    img_a = Image.new("RGB", (8, 8), "red")
    service = _FakeService(
        captures=[
            (img_a, "screen_region_gdi"),
            (img_a, "screen_region_gdi"),
            (img_a, "screen_region_gdi"),
        ]
    )
    progress: list[scroll_capture.ScrollCaptureProgress] = []
    result = scroll_capture.run_full_page_capture(
        service=service,
        target_hwnd=4242,
        options=scroll_capture.ScrollCaptureOptions(
            max_capture_pages=5,
            repeated_frame_stop_count=1,
            scroll_strategy="hybrid_wheel_pagedown",
        ),
        stop_requested=lambda: False,
        progress_callback=progress.append,
    )

    assert result.captured_frames == 1
    assert result.stop_reason == "repeat_detected"
    assert progress[-1].stop_reason == "repeat_detected"
    assert "no movement" in progress[-1].message.lower()


def test_run_full_capture_reports_user_stop(monkeypatch) -> None:
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
        options=scroll_capture.ScrollCaptureOptions(max_capture_pages=5),
        stop_requested=_stop_requested,
    )

    assert result.captured_frames == 1
    assert result.stop_reason == "user_stop"
    assert service.scroll_calls == []
