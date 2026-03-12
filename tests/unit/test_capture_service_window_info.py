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


def test_capture_candidate_can_include_minimized(monkeypatch) -> None:
    monkeypatch.setattr(capture_service.USER32, "IsWindowVisible", lambda _hwnd: 1)
    monkeypatch.setattr(capture_service.USER32, "IsIconic", lambda _hwnd: 1)

    assert not WindowCaptureService._is_capture_candidate(123, include_minimized=False)
    assert WindowCaptureService._is_capture_candidate(123, include_minimized=True)


def test_activate_window_restores_minimized_target(monkeypatch) -> None:
    iconic_state = {"value": 1}
    show_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(capture_service.USER32, "IsWindowVisible", lambda _hwnd: 1)
    monkeypatch.setattr(
        capture_service.USER32, "IsIconic", lambda _hwnd: iconic_state["value"]
    )

    def _show_window(hwnd: int, cmd: int) -> int:
        show_calls.append((int(hwnd), int(cmd)))
        iconic_state["value"] = 0
        return 1

    monkeypatch.setattr(capture_service.USER32, "ShowWindow", _show_window)
    monkeypatch.setattr(capture_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        WindowCaptureService,
        "is_foreground_window",
        staticmethod(lambda _hwnd: True),
    )

    focused, reason = WindowCaptureService.activate_window(4242)

    assert focused
    assert reason == ""
    assert show_calls == [(4242, capture_service.SW_RESTORE)]
