from __future__ import annotations

from web_pagez_to_pdf.capture_service import WindowInfo


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
