from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QPixmap

from web_pagez_to_pdf.capture_service import WindowInfo
from web_pagez_to_pdf.main_window import MainWindow
from web_pagez_to_pdf.mini_editor import MiniEditorWindow
from web_pagez_to_pdf.models import CaptureItem, EditAdjustments
from web_pagez_to_pdf.scroll_capture import ScrollCaptureProgress
from web_pagez_to_pdf.target_picker import PickedWindow

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch
    from pytestqt.qtbot import QtBot


def test_main_window_widget_identity_contract(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.property("widget_id") == "window:main"
    assert window.property("widget_alias") == "window"
    assert window.capture_button.property("widget_id") == "window:main:control:capture_button"
    assert window.capture_button.property("widget_alias") == "capture_button"
    assert (
        window.capture_last_selected_button.property("widget_id")
        == "window:main:control:capture_last_selected_button"
    )
    assert window.capture_backend_combo.property("widget_id") == "window:main:control:capture_backend_combo"
    assert (
        window.capture_scroll_strategy_combo.property("widget_id")
        == "window:main:control:capture_scroll_strategy_combo"
    )
    assert (
        window.capture_wheel_injection_combo.property("widget_id")
        == "window:main:control:capture_wheel_injection_combo"
    )
    assert (
        window.capture_center_click_assist_combo.property("widget_id")
        == "window:main:control:capture_center_click_assist_combo"
    )
    assert (
        window.capture_cursor_hold_combo.property("widget_id")
        == "window:main:control:capture_cursor_hold_combo"
    )
    assert (
        window.capture_log_level_combo.property("widget_id")
        == "window:main:control:capture_log_level_combo"
    )
    assert (
        window.capture_tab_preview_label.property("widget_id")
        == "window:main:control:capture_tab_preview_label"
    )
    assert window.capture_log_list.property("widget_id") == "window:main:control:capture_log_list"


def test_capture_advanced_group_is_visible_and_not_checkable(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.capture_advanced_group.isVisible()
    assert not window.capture_advanced_group.isCheckable()


def test_capture_last_selected_window_button_triggers_capture(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))

    def _resolve_second_last(_own_hwnd: int) -> int | None:
        return 4242

    def _window_title(_hwnd: int) -> str:
        return "last-selected"

    def _activate_window(_hwnd: int) -> tuple[bool, str]:
        return (True, "")

    def _capture_window(
        _hwnd: int, *, primary_backend: str = "screen_region_gdi"
    ) -> tuple[QPixmap | None, str]:
        pixmap = QPixmap(240, 120)
        pixmap.fill(Qt.GlobalColor.white)
        return (pixmap, primary_backend)

    window._capture_service.resolve_second_last_window = _resolve_second_last  # type: ignore[method-assign]
    window._capture_service.window_title = _window_title  # type: ignore[method-assign]
    window._capture_service.activate_window = _activate_window  # type: ignore[method-assign]
    window._capture_service.capture_window = _capture_window  # type: ignore[method-assign]

    qtbot.mouseClick(window.capture_last_selected_button, Qt.MouseButton.LeftButton)

    assert window._selected_target is not None
    assert window._selected_target.hwnd == 4242
    assert len(window._queue) == 1
    assert "captured selected viewport" in window.status_label.text().lower()


def test_capture_tab_thumbnail_updates_from_selected_queue_item(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))

    window._add_capture(
        image=Image.new("RGB", (180, 220), "blue"),
        title="sample",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()

    pixmap = window.capture_tab_preview_label.pixmap()
    assert pixmap is not None
    assert not pixmap.isNull()


def test_capture_progress_updates_status_and_log(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    payload = ScrollCaptureProgress(
        frame_index=2,
        backend_used="screen_region_gdi",
        scroll_method="wheel_center",
        diff_score=3.6,
        repeated_count=0,
        stop_reason="running",
        message="Frame 2 captured via screen_region_gdi (scroll=wheel_center, diff=3.60).",
    )
    window._on_full_capture_progress(payload)

    assert window.capture_log_list.count() >= 1
    assert "frame 2 captured" in window.capture_log_list.item(window.capture_log_list.count() - 1).text().lower()
    assert "full capture frame 2" in window.status_label.text().lower()


def test_capture_input_modes_persist_and_reload(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window._set_wheel_injection_combo("legacy_message_wheel")
    window._set_center_click_assist_combo("off")
    window._set_cursor_hold_combo("restore_each_step")
    window._set_capture_log_level_combo("DEBUG")
    payload = window._collect_settings_payload()

    assert str(payload["capture.wheel_injection_mode"]) == "legacy_message_wheel"
    assert str(payload["capture.center_click_assist"]) == "off"
    assert str(payload["capture.cursor_hold_mode"]) == "restore_each_step"
    assert str(payload["capture.log_level"]) == "DEBUG"


def test_capture_log_level_combo_normalizes_values(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window._set_capture_log_level_combo("DEBUG")
    assert str(window.capture_log_level_combo.currentData()) == "DEBUG"

    window._set_capture_log_level_combo("not-a-level")
    assert str(window.capture_log_level_combo.currentData()) == "DEBUG"


def test_default_browser_target_selected_on_start(qtbot: QtBot, monkeypatch: MonkeyPatch) -> None:
    def _list_top_windows(_self, _own_hwnd: int) -> list[WindowInfo]:
        return [
            WindowInfo(
                hwnd=1111,
                title="Notes",
                process_name="notepad.exe",
                class_name="Notepad",
            ),
            WindowInfo(
                hwnd=2222,
                title="Docs",
                process_name="chrome.exe",
                class_name="Chrome_WidgetWin_1",
            ),
        ]

    monkeypatch.setattr(
        "web_pagez_to_pdf.capture_service.WindowCaptureService.list_top_windows",
        _list_top_windows,
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window._selected_target is not None
    assert window._selected_target.hwnd == 2222
    assert "default browser target selected" in window.status_label.text().lower()


def test_full_capture_requires_focus_before_start(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._set_target(PickedWindow(hwnd=4242, label="target"))

    def _focus_fail(_hwnd: int) -> tuple[bool, str]:
        return (False, "could not focus")

    window._capture_service.ensure_window_foreground = _focus_fail  # type: ignore[method-assign]
    window._capture_full_scroll()

    assert window._capture_worker is None
    assert "could not focus" in window.status_label.text().lower()


def test_full_capture_finished_restores_focus_to_app(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    called: dict[str, int] = {"hwnd": 0}

    def _ensure_window_foreground(hwnd: int) -> tuple[bool, str]:
        called["hwnd"] = hwnd
        return (True, "")

    window._capture_service.ensure_window_foreground = _ensure_window_foreground  # type: ignore[method-assign]
    window._full_capture_finished()

    assert called["hwnd"] == int(window.winId())
    assert "focus returned" in window.capture_log_list.item(window.capture_log_list.count() - 1).text().lower()


def test_file_exit_action_has_required_shortcuts(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    actions = [action for action in window.menuBar().actions() if action.menu() is not None]
    file_menu = next((action.menu() for action in actions if action.text() == "File"), None)
    assert file_menu is not None
    exit_action = next((action for action in file_menu.actions() if action.text() == "E&xit"), None)
    assert isinstance(exit_action, QAction)
    shortcut_texts = {shortcut.toString() for shortcut in exit_action.shortcuts()}
    assert "Ctrl+Q" in shortcut_texts
    assert "Alt+X" in shortcut_texts


def test_editor_zoom_defaults_fit_height_and_manual_controls(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (120, 260), "green"),
        title="zoom",
        source_hwnd=None,
        frame_count=1,
    )

    assert window._editor_zoom_mode == "fit_height"
    assert "fit height" in window.zoom_status_label.text().lower()

    qtbot.mouseClick(window.zoom_100_button, Qt.MouseButton.LeftButton)
    assert window._editor_zoom_mode == "manual"
    assert "100%" in window.zoom_status_label.text()


def test_mini_editor_zoom_defaults_fit_height(qtbot: QtBot, tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (400, 900), "red").save(image_path, format="PNG")

    item = CaptureItem(
        item_id="item-1",
        title="sample",
        image_path=image_path,
        source_hwnd=None,
        frame_count=1,
    )
    window = MiniEditorWindow()
    qtbot.addWidget(window)
    window.show()
    window.bind_item(item, EditAdjustments())

    assert "fit height" in window.zoom_status_label.text().lower()
    qtbot.mouseClick(window.zoom_100_button, Qt.MouseButton.LeftButton)
    assert "100%" in window.zoom_status_label.text()
