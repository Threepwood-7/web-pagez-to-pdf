from __future__ import annotations

import web_pagez_to_pdf.capture_service as capture_service
from web_pagez_to_pdf.capture_service import WindowCaptureService, WindowInfo


def test_window_info_label_uses_process_title_pid_hwnd_format() -> None:
    info = WindowInfo(
        hwnd=264712,
        title="PornHub Home Page",
        process_name="chrome.exe",
        class_name="Chrome_WidgetWin_1",
        process_id=1234,
    )

    assert info.label == "chrome - PornHub Home Page [1234, 264712]"


def test_window_info_sort_key_uses_normalized_process_then_title() -> None:
    left = WindowInfo(
        hwnd=2,
        title="B page",
        process_name="Chrome.EXE",
        class_name="Chrome_WidgetWin_1",
        process_id=11,
    )
    right = WindowInfo(
        hwnd=1,
        title="A page",
        process_name="chrome.exe",
        class_name="Chrome_WidgetWin_1",
        process_id=10,
    )

    assert sorted([left, right], key=lambda item: item.sort_key) == [right, left]


def test_alt_tab_target_candidate_rejects_shell_classes(monkeypatch) -> None:
    monkeypatch.setattr(
        WindowCaptureService,
        "_is_capture_candidate",
        staticmethod(lambda _hwnd: True),
    )
    monkeypatch.setattr(
        WindowCaptureService,
        "window_class_name",
        staticmethod(lambda _hwnd: "Shell_TrayWnd"),
    )

    assert not WindowCaptureService._is_alt_tab_target_candidate(123, 999)


def test_resolve_alt_tab_target_retries_once(monkeypatch) -> None:
    service = WindowCaptureService()
    calls: dict[str, int] = {"shortcut": 0}

    monkeypatch.setattr(
        service,
        "_emit_alt_tab_shortcut",
        lambda: calls.__setitem__("shortcut", calls["shortcut"] + 1),
    )
    monkeypatch.setattr(capture_service.time, "sleep", lambda _seconds: None)
    sequence = [111, 222]
    monkeypatch.setattr(
        capture_service.USER32,
        "GetForegroundWindow",
        lambda: sequence.pop(0) if sequence else 0,
    )
    monkeypatch.setattr(
        service,
        "_is_alt_tab_target_candidate",
        lambda hwnd, own_hwnd: int(hwnd) == 222 and int(own_hwnd) == 999,
    )
    monkeypatch.setattr(service, "record_foreground_window", lambda: 222)

    hwnd = service.resolve_alt_tab_target(999, retries=1, settle_ms=0)

    assert hwnd == 222
    assert calls["shortcut"] == 2
