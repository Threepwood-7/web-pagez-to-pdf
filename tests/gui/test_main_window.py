from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import QApplication, QFormLayout
from reportlab.lib.units import mm

from web_pagez_to_pdf.capture_service import WindowInfo
from web_pagez_to_pdf.constants import APP_IDENTITY
from web_pagez_to_pdf.exporters import ExportResult
from web_pagez_to_pdf.image_processing import PAPER_SIZES, apply_edit_transform, compute_page_slices
from web_pagez_to_pdf.main_window import (
    THUMBNAIL_BORDER_CUE_COLOR,
    THUMBNAIL_GUTTER_CUE_COLOR,
    THUMBNAIL_MARGIN_CUE_COLOR,
    MainWindow,
)
from web_pagez_to_pdf.models import PrintLayout
from web_pagez_to_pdf.scroll_capture import ScrollCaptureProgress
from web_pagez_to_pdf.target_picker import PickedWindow

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch
    from pytestqt.qtbot import QtBot


def _line_cue_image(width: int = 420, height: int = 280) -> Image.Image:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for x_pos in (120, 300):
        draw.line((x_pos, 0, x_pos, height - 1), fill="black", width=2)
    for y_pos in (80, 220):
        draw.line((0, y_pos, width - 1, y_pos), fill="black", width=2)
    return image


def _has_near_color(image: Image.Image, target: tuple[int, int, int], tolerance: int = 16) -> bool:
    rgb = image.convert("RGB")
    px = rgb.load()
    for y_pos in range(rgb.height):
        for x_pos in range(rgb.width):
            red, green, blue = px[x_pos, y_pos]
            if (
                abs(int(red) - int(target[0])) <= tolerance
                and abs(int(green) - int(target[1])) <= tolerance
                and abs(int(blue) - int(target[2])) <= tolerance
            ):
                return True
    return False


def _sample_viewport_pixel(widget, point: QPoint):
    pixmap = widget.grab()
    image = pixmap.toImage()
    dpr = max(1.0, float(pixmap.devicePixelRatio()))
    x_pos = int(round(float(point.x()) * dpr))
    y_pos = int(round(float(point.y()) * dpr))
    x_pos = max(0, min(image.width() - 1, x_pos))
    y_pos = max(0, min(image.height() - 1, y_pos))
    return image.pixelColor(x_pos, y_pos)


def _find_color_bbox(
    image: Image.Image,
    *,
    red_min: int,
    green_max: int,
    blue_max: int,
) -> tuple[int, int, int, int] | None:
    rgb = image.convert("RGB")
    px = rgb.load()
    min_x = rgb.width
    min_y = rgb.height
    max_x = -1
    max_y = -1
    for y_pos in range(rgb.height):
        for x_pos in range(rgb.width):
            red, green, blue = px[x_pos, y_pos]
            if int(red) >= red_min and int(green) <= green_max and int(blue) <= blue_max:
                min_x = min(min_x, x_pos)
                min_y = min(min_y, y_pos)
                max_x = max(max_x, x_pos)
                max_y = max(max_y, y_pos)
    if max_x < min_x or max_y < min_y:
        return None
    return (min_x, min_y, max_x, max_y)


def _find_exact_color_bbox(image: Image.Image, color: tuple[int, int, int]) -> tuple[int, int, int, int] | None:
    rgb = image.convert("RGB")
    px = rgb.load()
    min_x = rgb.width
    min_y = rgb.height
    max_x = -1
    max_y = -1
    for y_pos in range(rgb.height):
        for x_pos in range(rgb.width):
            if tuple(int(v) for v in px[x_pos, y_pos]) == tuple(int(v) for v in color):
                min_x = min(min_x, x_pos)
                min_y = min(min_y, y_pos)
                max_x = max(max_x, x_pos)
                max_y = max(max_y, y_pos)
    if max_x < min_x or max_y < min_y:
        return None
    return (min_x, min_y, max_x, max_y)


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
        window.capture_scroll_mode_combo.property("widget_id")
        == "window:main:control:capture_scroll_mode_combo"
    )
    assert window.capture_wheel_injection_combo.property("widget_id") == "window:main:control:capture_wheel_injection_combo"
    assert (
        window.capture_cursor_hold_combo.property("widget_id")
        == "window:main:control:capture_cursor_hold_combo"
    )
    assert (
        window.capture_log_level_combo.property("widget_id")
        == "window:main:control:capture_log_level_combo"
    )
    assert (
        window.capture_frame_region_combo.property("widget_id")
        == "window:main:control:capture_frame_region_combo"
    )
    assert (
        window.capture_include_mouse_checkbox.property("widget_id")
        == "window:main:control:capture_include_mouse_checkbox"
    )
    assert (
        window.capture_scroll_to_top_checkbox.property("widget_id")
        == "window:main:control:capture_scroll_to_top_checkbox"
    )
    assert (
        window.capture_auto_trim_fixed_checkbox.property("widget_id")
        == "window:main:control:capture_auto_trim_fixed_checkbox"
    )
    assert (
        window.capture_viewport_options_group.property("widget_id")
        == "window:main:control:capture_viewport_options_group"
    )
    assert (
        window.capture_full_scroll_options_group.property("widget_id")
        == "window:main:control:capture_full_scroll_options_group"
    )
    assert (
        window.capture_shared_diagnostics_group.property("widget_id")
        == "window:main:control:capture_shared_diagnostics_group"
    )
    assert (
        window.capture_viewport_hint_label.property("widget_id")
        == "window:main:control:capture_viewport_hint_label"
    )
    assert (
        window.capture_tab_preview_label.property("widget_id")
        == "window:main:control:capture_tab_preview_label"
    )
    assert (
        window.capture_target_actions_group.property("widget_id")
        == "window:main:control:capture_target_actions_group"
    )
    assert (
        window.capture_thumbnail_group.property("widget_id")
        == "window:main:control:capture_thumbnail_group"
    )
    assert (
        window.capture_log_group.property("widget_id")
        == "window:main:control:capture_log_group"
    )
    assert window.capture_log_list.property("widget_id") == "window:main:control:capture_log_list"
    assert (
        window.layout_preview_group.property("widget_id")
        == "window:main:control:layout_preview_group"
    )
    assert (
        window.export_formats_group.property("widget_id")
        == "window:main:control:export_formats_group"
    )
    assert (
        window.export_output_group.property("widget_id")
        == "window:main:control:export_output_group"
    )
    assert (
        window.export_run_group.property("widget_id")
        == "window:main:control:export_run_group"
    )
    assert (
        window.open_after_export_checkbox.property("widget_id")
        == "window:main:control:open_after_export_checkbox"
    )
    assert (
        window.editor_overlay_toggle.property("widget_id")
        == "window:main:control:editor_overlay_toggle"
    )
    assert (
        window.page_preview_list.property("widget_id")
        == "window:main:control:page_preview_list"
    )
    assert (
        window.thumbnail_zoom_slider.property("widget_id")
        == "window:main:control:thumbnail_zoom_slider"
    )
    assert (
        window.editor_view_zoom_spin.property("widget_id")
        == "window:main:control:editor_view_zoom_spin"
    )
    assert not hasattr(window, "split_edit_tool_button")
    assert not hasattr(window, "quick_zoom_group")
    assert not hasattr(window, "quick_zoom_spin")
    assert not hasattr(window, "quick_zoom_preview_label")
    assert window.wizardry_group.property("widget_id") == "window:main:control:wizardry_group"
    assert (
        window.wizard_apply_queue_checkbox.property("widget_id")
        == "window:main:control:wizard_apply_queue_checkbox"
    )
    assert (
        window.wizard_auto_vertical_border_crop_button.property("widget_id")
        == "window:main:control:wizard_auto_vertical_border_crop_button"
    )
    assert (
        window.wizard_remove_scrollbar_button.property("widget_id")
        == "window:main:control:wizard_remove_scrollbar_button"
    )
    assert (
        window.wizard_remove_border_button.property("widget_id")
        == "window:main:control:wizard_remove_border_button"
    )
    assert window.wizard_undo_button.property("widget_id") == "window:main:control:wizard_undo_button"
    assert window.wizard_redo_button.property("widget_id") == "window:main:control:wizard_redo_button"


def test_capture_advanced_group_is_visible_and_not_checkable(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.capture_advanced_group.isVisible()
    assert not window.capture_advanced_group.isCheckable()
    assert window.capture_viewport_options_group.isVisible()
    assert window.capture_full_scroll_options_group.isVisible()
    assert window.capture_shared_diagnostics_group.isVisible()


def test_capture_advanced_controls_grouped_by_capture_type(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert _is_descendant(window.capture_viewport_hint_label, window.capture_viewport_options_group)

    assert _is_descendant(window.max_pages_spin, window.capture_full_scroll_options_group)
    assert _is_descendant(window.capture_delay_spin, window.capture_full_scroll_options_group)
    assert _is_descendant(window.capture_scroll_mode_combo, window.capture_full_scroll_options_group)
    assert _is_descendant(window.capture_scroll_to_top_checkbox, window.capture_full_scroll_options_group)
    assert _is_descendant(window.capture_auto_trim_fixed_checkbox, window.capture_full_scroll_options_group)
    assert _is_descendant(window.capture_wheel_injection_combo, window.capture_full_scroll_options_group)
    assert _is_descendant(window.capture_cursor_hold_combo, window.capture_full_scroll_options_group)

    assert _is_descendant(window.capture_backend_combo, window.capture_shared_diagnostics_group)
    assert _is_descendant(window.capture_frame_region_combo, window.capture_shared_diagnostics_group)
    assert _is_descendant(window.capture_include_mouse_checkbox, window.capture_shared_diagnostics_group)
    assert _is_descendant(window.capture_log_level_combo, window.capture_shared_diagnostics_group)


def test_capture_last_selected_window_button_triggers_capture(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))

    def _resolve_alt_tab_target(_own_hwnd: int, *, retries: int = 1, settle_ms: int = 180) -> int | None:
        _unused = retries
        _unused2 = settle_ms
        return 4242

    def _window_title(_hwnd: int) -> str:
        return "last-selected"

    def _activate_window(_hwnd: int) -> tuple[bool, str]:
        return (True, "")

    def _capture_window(
        _hwnd: int,
        *,
        primary_backend: str = "screen_region_gdi",
        frame_region: str = "client_area",
        include_mouse_cursor: bool = False,
    ) -> tuple[QPixmap | None, str]:
        pixmap = QPixmap(240, 120)
        pixmap.fill(Qt.GlobalColor.white)
        return (pixmap, primary_backend)

    window._capture_service.resolve_alt_tab_target = _resolve_alt_tab_target  # type: ignore[method-assign]
    window._capture_service.window_title = _window_title  # type: ignore[method-assign]
    window._capture_service.activate_window = _activate_window  # type: ignore[method-assign]
    window._capture_service.capture_window = _capture_window  # type: ignore[method-assign]

    qtbot.mouseClick(window.capture_last_selected_button, Qt.MouseButton.LeftButton)

    assert window._selected_target is not None
    assert window._selected_target.hwnd == 4242
    assert len(window._queue) == 1
    assert "captured selected viewport" in window.status_label.text().lower()


def test_capture_last_selected_restores_focus_to_app(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))

    window._capture_service.resolve_alt_tab_target = (  # type: ignore[method-assign]
        lambda _own_hwnd, retries=1, settle_ms=180: 5151
    )
    window._capture_service.window_title = lambda _hwnd: "alt-tab-target"  # type: ignore[method-assign]
    window._capture_service.activate_window = lambda _hwnd: (True, "")  # type: ignore[method-assign]

    ensured: dict[str, int] = {"hwnd": 0}

    def _ensure_window_foreground(hwnd: int) -> tuple[bool, str]:
        ensured["hwnd"] = hwnd
        return (True, "")

    def _capture_window(
        _hwnd: int,
        *,
        primary_backend: str = "screen_region_gdi",
        frame_region: str = "client_area",
        include_mouse_cursor: bool = False,
    ) -> tuple[QPixmap | None, str]:
        _unused = frame_region
        _unused2 = include_mouse_cursor
        pixmap = QPixmap(220, 140)
        pixmap.fill(Qt.GlobalColor.white)
        return (pixmap, primary_backend)

    window._capture_service.ensure_window_foreground = _ensure_window_foreground  # type: ignore[method-assign]
    window._capture_service.capture_window = _capture_window  # type: ignore[method-assign]

    qtbot.mouseClick(window.capture_last_selected_button, Qt.MouseButton.LeftButton)

    assert ensured["hwnd"] == int(window.winId())


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

    window._set_scroll_mode_combo("wheel_click_pagedown")
    window._set_capture_frame_region_combo("full_window")
    window.capture_scroll_to_top_checkbox.setChecked(False)
    window.capture_auto_trim_fixed_checkbox.setChecked(False)
    window._set_wheel_injection_combo("legacy_message_wheel")
    window._set_cursor_hold_combo("restore_each_step")
    window.capture_include_mouse_checkbox.setChecked(True)
    window._set_capture_log_level_combo("DEBUG")
    payload = window._collect_settings_payload()

    assert str(payload["capture.scroll_mode"]) == "wheel_click_pagedown"
    assert str(payload["capture.frame_region"]) == "full_window"
    assert bool(payload["capture.scroll_to_top_on_full"]) is False
    assert bool(payload["capture.auto_trim_fixed_strips"]) is False
    assert str(payload["capture.wheel_injection_mode"]) == "legacy_message_wheel"
    assert str(payload["capture.cursor_hold_mode"]) == "restore_each_step"
    assert bool(payload["capture.include_mouse_cursor"]) is True
    assert str(payload["capture.log_level"]) == "DEBUG"


def test_scroll_to_top_checkbox_defaults_enabled(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.capture_scroll_to_top_checkbox.isChecked()


def test_auto_trim_fixed_checkbox_defaults_enabled(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.capture_auto_trim_fixed_checkbox.isChecked()


def test_capture_defaults_max_pages_and_delay(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.max_pages_spin.value() == 50
    assert window.capture_delay_spin.value() == 333


def test_wizard_apply_queue_checkbox_defaults_disabled(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert not window.wizard_apply_queue_checkbox.isChecked()


def test_capture_log_level_combo_normalizes_values(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window._set_capture_log_level_combo("DEBUG")
    assert str(window.capture_log_level_combo.currentData()) == "DEBUG"

    window._set_capture_log_level_combo("not-a-level")
    assert str(window.capture_log_level_combo.currentData()) == "DEBUG"


def test_pick_button_uses_menu_and_crosshair_button_removed(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.pick_list_button.menu() is not None
    assert not hasattr(window, "pick_crosshair_button")


def test_capture_actions_live_in_capture_tab_and_no_toolbar(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    capture_tab = window.tabs.widget(0)
    assert not hasattr(window, "top_toolbar")
    assert _is_descendant(window.capture_target_actions_group, capture_tab)
    assert _is_descendant(window.target_label, capture_tab)
    assert _is_descendant(window.pick_list_button, capture_tab)
    assert _is_descendant(window.capture_button, capture_tab)
    assert _is_descendant(window.capture_full_button, capture_tab)
    assert _is_descendant(window.capture_last_selected_button, capture_tab)
    assert _is_descendant(window.stop_button, capture_tab)
    assert _is_descendant(window.import_button, capture_tab)
    assert not hasattr(window, "quick_export_button")
    assert not hasattr(window, "quick_pdf_checkbox")


def test_capture_target_row_places_picker_before_target_label(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    group_layout = window.capture_target_actions_group.layout()
    assert group_layout is not None
    first_row_item = group_layout.itemAt(0)
    assert first_row_item is not None
    first_row_layout = first_row_item.layout()
    assert first_row_layout is not None
    first_widget = first_row_layout.itemAt(0).widget()
    second_widget = first_row_layout.itemAt(1).widget()
    assert first_widget is window.pick_list_button
    assert second_widget is window.target_label


def test_pick_menu_sorted_and_target_label_format(
    qtbot: QtBot, monkeypatch: MonkeyPatch
) -> None:
    windows = [
        WindowInfo(
            hwnd=4002,
            title="B Site",
            process_name="chrome.exe",
            class_name="Chrome_WidgetWin_1",
            process_id=222,
        ),
        WindowInfo(
            hwnd=4001,
            title="A Site",
            process_name="chrome.exe",
            class_name="Chrome_WidgetWin_1",
            process_id=111,
        ),
        WindowInfo(
            hwnd=3000,
            title="Main",
            process_name="firefox.exe",
            class_name="MozillaWindowClass",
            process_id=333,
            is_minimized=True,
        ),
    ]

    monkeypatch.setattr(
        "web_pagez_to_pdf.capture_service.WindowCaptureService.list_top_windows",
        lambda _self, _own_hwnd, include_minimized=False: (
            windows
            if include_minimized
            else [item for item in windows if not item.is_minimized]
        ),
    )
    monkeypatch.setattr(
        "web_pagez_to_pdf.capture_service.WindowCaptureService.window_info",
        lambda _self, hwnd: next((item for item in windows if item.hwnd == hwnd), None),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._populate_pick_target_menu()

    actions = [action for action in window.pick_target_menu.actions() if not action.isSeparator()]
    assert actions[0].text() == "Pick with Crosshair..."
    assert actions[1].text() == "chrome - A Site [111, 4001]"
    assert actions[2].text() == "chrome - B Site [222, 4002]"
    assert actions[3].text() == "firefox - Main [333, 3000] (minimized)"

    actions[2].trigger()
    assert window._selected_target is not None
    assert window.target_label.text() == "Target: chrome - B Site [222, 4002]"


def test_default_browser_target_selected_on_start(qtbot: QtBot, monkeypatch: MonkeyPatch) -> None:
    def _list_top_windows(
        _self,
        _own_hwnd: int,
        *,
        include_minimized: bool = False,
    ) -> list[WindowInfo]:
        _unused = include_minimized
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


def test_viewport_capture_auto_resolves_target_when_none_selected(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._selected_target = None

    window._capture_service.resolve_second_last_window = lambda _own_hwnd: 5151  # type: ignore[method-assign]
    window._capture_service.window_title = lambda _hwnd: "auto-target"  # type: ignore[method-assign]
    window._capture_service.activate_window = lambda _hwnd: (True, "")  # type: ignore[method-assign]

    def _capture_window(
        _hwnd: int,
        *,
        primary_backend: str = "screen_region_gdi",
        frame_region: str = "client_area",
        include_mouse_cursor: bool = False,
    ) -> tuple[QPixmap | None, str]:
        pixmap = QPixmap(160, 90)
        pixmap.fill(Qt.GlobalColor.white)
        return (pixmap, primary_backend)

    window._capture_service.capture_window = _capture_window  # type: ignore[method-assign]

    window._capture_selected_viewport()

    assert window._selected_target is not None
    assert window._selected_target.hwnd == 5151
    assert len(window._queue) == 1


def test_viewport_capture_does_not_auto_trim_scrollbar(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._set_target(PickedWindow(hwnd=7070, label="viewport-target", title="viewport-target"))

    window._capture_service.activate_window = lambda _hwnd: (True, "")  # type: ignore[method-assign]

    def _capture_window(
        _hwnd: int,
        *,
        primary_backend: str = "screen_region_gdi",
        frame_region: str = "client_area",
        include_mouse_cursor: bool = False,
    ) -> tuple[QPixmap | None, str]:
        _unused = frame_region
        _unused2 = include_mouse_cursor
        pixmap = QPixmap(161, 91)
        pixmap.fill(Qt.GlobalColor.white)
        return (pixmap, primary_backend)

    window._capture_service.capture_window = _capture_window  # type: ignore[method-assign]

    window._capture_selected_viewport()

    assert len(window._queue) == 1
    captured = Image.open(window._queue[0].image_path).convert("RGB")
    assert captured.width == 161
    assert "auto-trim scrollbar" not in window.status_label.text().lower()
    messages = [
        window.capture_log_list.item(index).text().lower()
        for index in range(window.capture_log_list.count())
    ]
    assert not any("auto-trimmed right scrollbar" in text for text in messages)


def test_full_capture_forces_scrollbar_auto_trim_enabled(qtbot: QtBot) -> None:
    class _DummySignal:
        def connect(self, _slot) -> None:
            return

    captured_options: list[object] = []

    class _FakeWorker:
        def __init__(self, capture_service, target_hwnd, options, stop_event) -> None:
            _unused = capture_service
            _unused2 = target_hwnd
            _unused3 = stop_event
            captured_options.append(options)
            self.started = _DummySignal()
            self.capture_succeeded = _DummySignal()
            self.capture_failed = _DummySignal()
            self.capture_progress = _DummySignal()
            self.finished = _DummySignal()

        def start(self) -> None:
            return

        def isRunning(self) -> bool:
            return False

        def run(self) -> None:
            return

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._set_target(PickedWindow(hwnd=6060, label="full-target"))
    window._capture_service.ensure_window_foreground = lambda _hwnd: (True, "")  # type: ignore[method-assign]

    import web_pagez_to_pdf.main_window as main_window_module

    old_worker = main_window_module.FullCaptureWorker
    try:
        main_window_module.FullCaptureWorker = _FakeWorker  # type: ignore[assignment]
        window._capture_full_scroll()
    finally:
        main_window_module.FullCaptureWorker = old_worker  # type: ignore[assignment]

    assert captured_options
    assert bool(getattr(captured_options[0], "auto_trim_scrollbar", False)) is True


def test_full_capture_auto_resolves_target_when_none_selected(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._selected_target = None

    window._capture_service.resolve_second_last_window = lambda _own_hwnd: 6262  # type: ignore[method-assign]
    window._capture_service.window_title = lambda _hwnd: "auto-target-full"  # type: ignore[method-assign]
    called: dict[str, int] = {"hwnd": 0}

    def _focus_fail(hwnd: int) -> tuple[bool, str]:
        called["hwnd"] = hwnd
        return (False, "could not focus")

    window._capture_service.ensure_window_foreground = _focus_fail  # type: ignore[method-assign]
    window._capture_full_scroll()

    assert window._selected_target is not None
    assert window._selected_target.hwnd == 6262
    assert called["hwnd"] == 6262
    assert window._capture_worker is None


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


def test_edit_menu_has_undo_redo_shortcuts(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    actions = [action for action in window.menuBar().actions() if action.menu() is not None]
    edit_menu = next((action.menu() for action in actions if action.text() == "Edit"), None)
    assert edit_menu is not None
    undo_action = next((action for action in edit_menu.actions() if action.text() == "Undo"), None)
    redo_action = next((action for action in edit_menu.actions() if action.text() == "Redo"), None)
    assert isinstance(undo_action, QAction)
    assert isinstance(redo_action, QAction)
    assert undo_action.shortcut().toString() == "Ctrl+Z"
    assert redo_action.shortcut().toString() == "Ctrl+Y"


def test_startup_tab_forced_capture_even_if_settings_saved_other_tab(qtbot: QtBot) -> None:
    settings = QSettings()
    settings.setValue("ui.start_tab", "export")
    settings.sync()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.tabs.currentIndex() == 0


def test_editor_zoom_defaults_fit_width_and_manual_controls(qtbot: QtBot, tmp_path: Path) -> None:
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

    assert "fit width" in window.zoom_status_label.text().lower()
    assert not window.editor_view_zoom_spin.isEnabled()

    qtbot.mouseClick(window.zoom_100_button, Qt.MouseButton.LeftButton)
    assert "100%" in window.zoom_status_label.text()
    assert window.editor_view_zoom_spin.isEnabled()
    assert window.editor_view_zoom_spin.value() == 100

    qtbot.mouseClick(window.zoom_in_button, Qt.MouseButton.LeftButton)
    assert window.editor_view_zoom_spin.value() == 110

    window.editor_view_zoom_spin.setValue(140)
    assert "140%" in window.zoom_status_label.text()

    window.editor_canvas.adjust_manual_zoom(-10)
    assert window.editor_view_zoom_spin.value() == 130

    qtbot.mouseClick(window.zoom_fit_width_button, Qt.MouseButton.LeftButton)
    assert "fit width" in window.zoom_status_label.text().lower()
    assert not window.editor_view_zoom_spin.isEnabled()


def test_editor_has_no_mini_editor_entry_point(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert not hasattr(window, "open_mini_editor_button")


def test_wizard_vertical_border_crop_is_additive_with_existing_crop(
    qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (240, 320), "white"),
        title="crop",
        source_hwnd=None,
        frame_count=1,
    )
    item = window._current_item()
    assert item is not None
    edits = window._session_for_item(item.item_id)
    edits.set_operation("crop_rect", {"left": 10, "top": 10, "width": 100, "height": 100})
    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_auto_vertical_border_crop_with_confidence",
        lambda _image: (14, 12, True, True),
    )

    window._run_wizard_auto_vertical_border_crop()

    assert edits.get_operation("crop_rect") is not None
    nav_crop = edits.get_operation("auto_vertical_border_crop")
    assert nav_crop is not None
    assert int(nav_crop.params.get("left", 0)) == 14
    assert int(nav_crop.params.get("right", 0)) == 12


def test_wizard_vertical_border_crop_queue_mode_applies_per_item_to_all_items(
    qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    for index in range(3):
        window._add_capture(
            image=Image.new("RGB", (200 + (index * 20), 220), "white"),
            title=f"item-{index}",
            source_hwnd=None,
            frame_count=1,
        )

    def _fake_border_crop(image: Image.Image) -> tuple[int, int, bool, bool]:
        if image.width == 200:
            return (12, 7, True, True)
        if image.width == 220:
            return (24, 15, True, True)
        return (0, 0, False, False)

    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_auto_vertical_border_crop_with_confidence",
        _fake_border_crop,
    )
    window.wizard_apply_queue_checkbox.setChecked(True)
    window._run_wizard_auto_vertical_border_crop()

    first = window._session_for_item(window._queue[0].item_id).get_operation(
        "auto_vertical_border_crop"
    )
    second = window._session_for_item(window._queue[1].item_id).get_operation(
        "auto_vertical_border_crop"
    )
    third = window._session_for_item(window._queue[2].item_id).get_operation(
        "auto_vertical_border_crop"
    )
    assert first is not None
    assert int(first.params.get("left", 0)) == 12
    assert int(first.params.get("right", 0)) == 7
    assert second is not None
    assert int(second.params.get("left", 0)) == 24
    assert int(second.params.get("right", 0)) == 15
    assert third is None


def test_wizard_vertical_border_crop_queue_mode_noop_on_insufficient_confidence(
    qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    for index in range(3):
        window._add_capture(
            image=Image.new("RGB", (210, 210), "white"),
            title=f"item-{index}",
            source_hwnd=None,
            frame_count=1,
        )
    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_auto_vertical_border_crop_with_confidence",
        lambda _image: (0, 0, False, False),
    )

    window.wizard_apply_queue_checkbox.setChecked(True)
    window._run_wizard_auto_vertical_border_crop()

    assert "insufficient confidence" in window.status_label.text().lower()
    for item in window._queue:
        edits = window._session_for_item(item.item_id)
        assert edits.get_operation("auto_vertical_border_crop") is None


def test_wizard_remove_scrollbar_sets_operation(qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (210, 200), "white"),
        title="scrollbar",
        source_hwnd=None,
        frame_count=1,
    )
    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_scrollbar_trim_with_confidence",
        lambda _image: (11, True),
    )

    window._run_wizard_remove_scrollbar()

    item = window._current_item()
    assert item is not None
    op = window._session_for_item(item.item_id).get_operation("wizard_scrollbar_trim")
    assert op is not None
    assert int(op.params.get("right", 0)) == 11


def test_wizard_remove_border_sets_operation(qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (220, 220), "white"),
        title="border",
        source_hwnd=None,
        frame_count=1,
    )
    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_window_border_trim_with_confidence",
        lambda _image: (4, 6, 3, 5, True, True, True, True),
    )

    window._run_wizard_remove_window_border()

    item = window._current_item()
    assert item is not None
    op = window._session_for_item(item.item_id).get_operation("wizard_border_trim")
    assert op is not None
    assert int(op.params.get("left", 0)) == 4
    assert int(op.params.get("right", 0)) == 6
    assert int(op.params.get("top", 0)) == 3
    assert int(op.params.get("bottom", 0)) == 5


def test_wizard_remove_border_accumulates_on_repeated_clicks(
    qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (220, 220), "white"),
        title="border-iterative",
        source_hwnd=None,
        frame_count=1,
    )
    seen_sizes: list[tuple[int, int]] = []
    responses = iter(
        [
            (4, 6, 3, 5, True, True, True, True),
            (1, 2, 0, 1, True, True, False, True),
        ]
    )

    def _fake_detector(image: Image.Image) -> tuple[int, int, int, int, bool, bool, bool, bool]:
        seen_sizes.append(image.size)
        return next(responses)

    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_window_border_trim_with_confidence",
        _fake_detector,
    )

    window._run_wizard_remove_window_border()
    window._run_wizard_remove_window_border()

    item = window._current_item()
    assert item is not None
    op = window._session_for_item(item.item_id).get_operation("wizard_border_trim")
    assert op is not None
    assert seen_sizes == [(220, 220), (210, 212)]
    assert int(op.params.get("left", 0)) == 5
    assert int(op.params.get("right", 0)) == 8
    assert int(op.params.get("top", 0)) == 3
    assert int(op.params.get("bottom", 0)) == 6


def test_wizard_remove_border_uncertain_second_pass_keeps_existing_trim(
    qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (220, 220), "white"),
        title="border-noop",
        source_hwnd=None,
        frame_count=1,
    )
    responses = iter(
        [
            (4, 6, 3, 5, True, True, True, True),
            (0, 0, 0, 0, False, False, False, False),
        ]
    )

    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_window_border_trim_with_confidence",
        lambda _image: next(responses),
    )

    window._run_wizard_remove_window_border()
    window._run_wizard_remove_window_border()

    item = window._current_item()
    assert item is not None
    op = window._session_for_item(item.item_id).get_operation("wizard_border_trim")
    assert op is not None
    assert int(op.params.get("left", 0)) == 4
    assert int(op.params.get("right", 0)) == 6
    assert int(op.params.get("top", 0)) == 3
    assert int(op.params.get("bottom", 0)) == 5
    assert len(window._undo_history) == 1


def test_wizard_remove_border_queue_iterative_per_item_one_undo_step(
    qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (220, 200), "white"),
        title="border-q0",
        source_hwnd=None,
        frame_count=1,
    )
    window._add_capture(
        image=Image.new("RGB", (240, 200), "white"),
        title="border-q1",
        source_hwnd=None,
        frame_count=1,
    )
    first_item = window._queue[0]
    second_item = window._queue[1]
    window._session_for_item(first_item.item_id).set_operation(
        "wizard_border_trim",
        {"left": 2, "right": 1, "top": 0, "bottom": 0},
    )
    window.wizard_apply_queue_checkbox.setChecked(True)

    def _fake_detector(image: Image.Image) -> tuple[int, int, int, int, bool, bool, bool, bool]:
        if image.size == (217, 200):
            return (1, 0, 2, 0, True, False, True, False)
        if image.size == (240, 200):
            return (0, 3, 0, 2, False, True, False, True)
        return (0, 0, 0, 0, False, False, False, False)

    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_window_border_trim_with_confidence",
        _fake_detector,
    )

    window._run_wizard_remove_window_border()

    first_op = window._session_for_item(first_item.item_id).get_operation("wizard_border_trim")
    second_op = window._session_for_item(second_item.item_id).get_operation("wizard_border_trim")
    assert first_op is not None
    assert second_op is not None
    assert int(first_op.params.get("left", 0)) == 3
    assert int(first_op.params.get("right", 0)) == 1
    assert int(first_op.params.get("top", 0)) == 2
    assert int(first_op.params.get("bottom", 0)) == 0
    assert int(second_op.params.get("left", 0)) == 0
    assert int(second_op.params.get("right", 0)) == 3
    assert int(second_op.params.get("top", 0)) == 0
    assert int(second_op.params.get("bottom", 0)) == 2
    assert len(window._undo_history) == 1

    window._undo_editor_change()
    first_op = window._session_for_item(first_item.item_id).get_operation("wizard_border_trim")
    second_op = window._session_for_item(second_item.item_id).get_operation("wizard_border_trim")
    assert first_op is not None
    assert int(first_op.params.get("left", 0)) == 2
    assert int(first_op.params.get("right", 0)) == 1
    assert int(first_op.params.get("top", 0)) == 0
    assert int(first_op.params.get("bottom", 0)) == 0
    assert second_op is None


def test_wizard_queue_run_is_one_undo_step(qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    for index in range(3):
        window._add_capture(
            image=Image.new("RGB", (200 + index * 5, 180), "white"),
            title=f"undo-queue-{index}",
            source_hwnd=None,
            frame_count=1,
        )
    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.suggest_scrollbar_trim_with_confidence",
        lambda _image: (12, True),
    )
    window.wizard_apply_queue_checkbox.setChecked(True)

    window._run_wizard_remove_scrollbar()
    assert len(window._undo_history) == 1

    window._undo_editor_change()
    for item in window._queue:
        edits = window._session_for_item(item.item_id)
        assert edits.get_operation("wizard_scrollbar_trim") is None


def test_global_undo_redo_applies_to_manual_crop(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (300, 220), "white"),
        title="undo-crop",
        source_hwnd=None,
        frame_count=1,
    )
    window.rect_crop_tool_button.click()
    from PySide6.QtCore import QRectF

    window._on_editor_rect_drawn("crop_rect", QRectF(10.0, 10.0, 80.0, 60.0))
    item = window._current_item()
    assert item is not None
    assert window._session_for_item(item.item_id).get_operation("crop_rect") is not None

    window._undo_editor_change()
    assert window._session_for_item(item.item_id).get_operation("crop_rect") is None

    window._redo_editor_change()
    assert window._session_for_item(item.item_id).get_operation("crop_rect") is not None


def _is_descendant(widget, parent) -> bool:
    current = widget
    while current is not None:
        if current is parent:
            return True
        current = current.parentWidget()
    return False


def test_layout_controls_live_in_editor_tab_not_export_tab(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    editor_tab = window.tabs.widget(1)
    export_tab = window.tabs.widget(2)

    assert _is_descendant(window.paper_combo, editor_tab)
    assert _is_descendant(window.header_input, editor_tab)
    assert _is_descendant(window.footer_input, editor_tab)
    assert not _is_descendant(window.paper_combo, export_tab)
    assert not _is_descendant(window.header_input, export_tab)
    assert not _is_descendant(window.footer_input, export_tab)


def test_layout_preview_has_no_quick_zoom_panel_and_header_footer_labels(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.tabs.setCurrentIndex(1)
    qtbot.wait(80)

    assert _is_descendant(window.paper_combo, window.layout_preview_group)
    assert not hasattr(window, "quick_zoom_group")
    assert isinstance(window.layout_preview_form, QFormLayout)
    assert (
        window.layout_preview_form.fieldGrowthPolicy()
        == QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
    )
    assert window.layout_paper_orientation_row.width() >= int(window.layout_preview_group.width() * 0.55)
    header_parent = window.header_label.parentWidget()
    footer_parent = window.footer_label.parentWidget()
    assert header_parent is not None and header_parent.layout() is not None
    assert footer_parent is not None and footer_parent.layout() is not None
    header_layout = header_parent.layout()
    footer_layout = footer_parent.layout()
    assert header_layout.indexOf(window.header_label) < header_layout.indexOf(window.header_input)
    assert footer_layout.indexOf(window.footer_label) < footer_layout.indexOf(window.footer_input)
    assert _is_descendant(window.page_preview_list, window.layout_preview_group)


def test_layout_preview_legend_colors_match_visual_cues(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    decorated = window._decorate_page_thumbnail(
        Image.new("RGB", (260, 180), (16, 16, 16)),
    )
    legend_band = decorated.crop((0, 0, decorated.width, 26))

    assert _has_near_color(
        legend_band,
        (
            int(THUMBNAIL_MARGIN_CUE_COLOR[0]),
            int(THUMBNAIL_MARGIN_CUE_COLOR[1]),
            int(THUMBNAIL_MARGIN_CUE_COLOR[2]),
        ),
    )
    assert _has_near_color(
        legend_band,
        (
            int(THUMBNAIL_GUTTER_CUE_COLOR[0]),
            int(THUMBNAIL_GUTTER_CUE_COLOR[1]),
            int(THUMBNAIL_GUTTER_CUE_COLOR[2]),
        ),
    )
    assert _has_near_color(
        legend_band,
        (
            int(THUMBNAIL_BORDER_CUE_COLOR[0]),
            int(THUMBNAIL_BORDER_CUE_COLOR[1]),
            int(THUMBNAIL_BORDER_CUE_COLOR[2]),
        ),
    )


def test_thumbnail_virtual_page_geometry_matches_export_layout(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    layout = PrintLayout(
        paper_name="A4",
        orientation="portrait",
        margin_top_mm=20.0,
        margin_bottom_mm=20.0,
        margin_left_mm=15.0,
        margin_right_mm=15.0,
        gutter_mm=12.0,
    )
    source_color = (30, 170, 30)
    source = Image.new("RGB", (260, 120), source_color)
    decorated = window._decorate_page_thumbnail(source, layout=layout)

    assert decorated.width > source.width
    assert decorated.height > source.height
    image_bbox = _find_exact_color_bbox(decorated, source_color)
    assert image_bbox is not None
    min_x, min_y, _max_x, max_y = image_bbox
    assert min_x > 2
    assert min_y > 2
    assert max_y < decorated.height - 6

    page_w_pt, page_h_pt = PAPER_SIZES["A4"]
    avail_w_pt = page_w_pt - (layout.margin_left_mm + layout.margin_right_mm + layout.gutter_mm) * mm
    px_per_pt = float(source.width) / float(avail_w_pt)
    expected_left = int(round(float(layout.margin_left_mm) * mm * px_per_pt))
    expected_top = int(round(float(layout.margin_top_mm) * mm * px_per_pt))
    expected_page_h = int(round(float(page_h_pt) * px_per_pt))
    expected_printable_right = int(
        round((float(page_w_pt) - (float(layout.margin_right_mm) + float(layout.gutter_mm)) * mm) * px_per_pt)
    )
    expected_right_margin_boundary = int(
        round((float(page_w_pt) - float(layout.margin_right_mm) * mm) * px_per_pt)
    )

    assert abs(min_x - expected_left) <= 3
    assert abs(min_y - expected_top) <= 3
    assert abs(decorated.height - expected_page_h) <= 3

    band_left = max(0, min(decorated.width - 2, expected_printable_right + 1))
    band_right = max(band_left + 1, min(decorated.width - 1, expected_right_margin_boundary - 1))
    band_top = max(28, expected_top + 8)
    band_bottom = min(decorated.height - 1, band_top + 30)
    assert band_right > band_left
    assert band_bottom > band_top
    gutter_band = decorated.crop((band_left, band_top, band_right, band_bottom))
    assert _has_near_color(
        gutter_band,
        (
            int(THUMBNAIL_GUTTER_CUE_COLOR[0]),
            int(THUMBNAIL_GUTTER_CUE_COLOR[1]),
            int(THUMBNAIL_GUTTER_CUE_COLOR[2]),
        ),
        tolerance=12,
    )


def test_overlay_toggle_defaults_on_and_updates_canvas(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.editor_overlay_toggle.isChecked()
    assert window.editor_canvas.overlay_visible()
    window.editor_overlay_toggle.setChecked(False)
    assert not window.editor_canvas.overlay_visible()


def test_page_preview_sidebar_count_matches_computed_slices(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (420, 1200), "white"),
        title="preview-count",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()
    item = window._current_item()
    assert item is not None
    source = Image.open(item.image_path).convert("RGB")
    transformed = apply_edit_transform(
        source,
        window._collect_layout(),
        window._session_for_item(item.item_id),
    )
    slices = compute_page_slices(
        transformed,
        window._collect_layout(),
        window._split_markers(),
    )

    assert window.page_preview_list.count() == len(slices)


def test_split_marker_and_layout_changes_refresh_page_preview_sidebar(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (420, 2400), "white"),
        title="preview-refresh",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()
    initial_count = window.page_preview_list.count()
    initial_slices = list(window._current_preview_slices)

    window.split_spin.setValue(120)
    window._add_split_marker()
    qtbot.waitUntil(
        lambda: any(bottom == 120 for _top, bottom in window._current_preview_slices)
    )
    with_marker_count = window.page_preview_list.count()
    assert window._current_preview_slices != initial_slices

    window.orientation_combo.setCurrentText("landscape")
    qtbot.waitUntil(lambda: window.page_preview_list.count() != with_marker_count)
    assert window.page_preview_list.count() != initial_count


def test_thumbnail_zoom_slider_and_zoom_signal_resize_preview_icons(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (420, 2200), "white"),
        title="thumbnail-zoom",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()
    initial_width = window.page_preview_list.iconSize().width()

    window.thumbnail_zoom_slider.setValue(initial_width + 40)
    assert window.page_preview_list.iconSize().width() == initial_width + 40

    window.page_preview_list.zoom_delta_requested.emit(-10)
    assert window.page_preview_list.iconSize().width() == initial_width + 30


def test_thumbnail_hover_overlay_appears_and_clears_on_leave(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (420, 1200), (220, 10, 10)),
        title="thumbnail-hover",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()
    window.tabs.setCurrentIndex(1)
    qtbot.wait(80)
    image_pixmap = window.editor_canvas._pixmap_item.pixmap()
    mapped_image = window.editor_canvas.mapFromScene(
        QRectF(0.0, 0.0, float(image_pixmap.width()), float(image_pixmap.height()))
    ).boundingRect()
    probe_rect = mapped_image.intersected(window.editor_canvas.viewport().rect())
    assert not probe_rect.isEmpty()
    probe_x = min(probe_rect.right() - 1, probe_rect.left() + 8)
    probe_view = QPoint(probe_x, probe_rect.center().y())
    before = _sample_viewport_pixel(window.editor_canvas.viewport(), probe_view)
    assert before.red() > 140

    item = window.page_preview_list.item(0)
    assert item is not None
    window._on_page_preview_item_hovered(item)
    qtbot.wait(50)
    overlay = window.editor_canvas._hover_overlay_pixmap
    assert overlay is not None
    overlay_image = overlay.toImage()
    sample = overlay_image.pixelColor(
        max(1, overlay_image.width() // 6),
        max(1, overlay_image.height() // 2),
    )
    assert sample.red() > 140
    masked = _sample_viewport_pixel(window.editor_canvas.viewport(), probe_view)
    assert masked.red() < 80
    assert masked.green() < 80
    assert masked.blue() < 80

    QApplication.sendEvent(window.page_preview_list.viewport(), QEvent(QEvent.Type.Leave))
    qtbot.wait(50)
    assert window.editor_canvas._hover_overlay_pixmap is None
    unmasked = _sample_viewport_pixel(window.editor_canvas.viewport(), probe_view)
    assert unmasked.red() > 140


def test_editor_scroll_resets_to_top_when_entering_editor_after_new_capture(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))

    window._add_capture(
        image=Image.new("RGB", (420, 4200), "white"),
        title="first",
        source_hwnd=None,
        frame_count=1,
    )
    window.tabs.setCurrentIndex(1)
    qtbot.wait(80)
    window.editor_canvas.focus_on_y(3900.0)
    qtbot.wait(80)
    assert (
        window.editor_canvas.verticalScrollBar().value()
        > window.editor_canvas.verticalScrollBar().minimum()
    )

    window.tabs.setCurrentIndex(0)
    window._add_capture(
        image=Image.new("RGB", (420, 4200), "white"),
        title="second",
        source_hwnd=None,
        frame_count=1,
    )
    window.tabs.setCurrentIndex(1)
    qtbot.waitUntil(
        lambda: window.editor_canvas.verticalScrollBar().value()
        == window.editor_canvas.verticalScrollBar().minimum()
    )


def test_run_export_single_format_updates_status_without_crash(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (240, 320), "white"),
        title="export",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window.pdf_checkbox.setChecked(False)
    window.paged_images_checkbox.setChecked(False)
    window.long_image_checkbox.setChecked(False)
    window.tiff_checkbox.setChecked(False)
    window.docx_checkbox.setChecked(False)
    window.pptx_checkbox.setChecked(False)
    window.xlsx_checkbox.setChecked(True)

    called: dict[str, int] = {"count": 0}

    def _fake_run_export(request) -> ExportResult:
        called["count"] += 1
        assert request.formats.xlsx is True
        assert request.formats.pdf is False
        out = tmp_path / "fake.xlsx"
        out.write_bytes(b"PK\x03\x04")
        return ExportResult(generated_paths=[out])

    monkeypatch.setattr("web_pagez_to_pdf.main_window.run_export", _fake_run_export)
    monkeypatch.setattr("web_pagez_to_pdf.main_window.os.startfile", lambda _path: None)

    window._run_export()

    assert called["count"] == 1
    assert "exported 1 file" in window.status_label.text().lower()


def test_open_after_export_launches_file_or_folder_by_output_count(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (240, 320), "white"),
        title="export-open",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window.pdf_checkbox.setChecked(False)
    window.xlsx_checkbox.setChecked(True)
    launched: list[str] = []

    monkeypatch.setattr("web_pagez_to_pdf.main_window.os.startfile", launched.append)

    single = tmp_path / "single.xlsx"
    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.run_export",
        lambda _request: ExportResult(generated_paths=[single]),
    )
    window.open_after_export_checkbox.setChecked(True)
    window._run_export()
    assert launched[-1] == str(single)

    first = tmp_path / "one.pdf"
    second = tmp_path / "two.xlsx"
    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.run_export",
        lambda _request: ExportResult(generated_paths=[first, second]),
    )
    window._run_export()
    assert launched[-1] == str(tmp_path)

    monkeypatch.setattr(
        "web_pagez_to_pdf.main_window.run_export",
        lambda _request: ExportResult(generated_paths=[single]),
    )
    window.open_after_export_checkbox.setChecked(False)
    launched_count = len(launched)
    window._run_export()
    assert len(launched) == launched_count


def _clear_window_state_settings() -> None:
    app = QApplication.instance()
    if app is not None:
        app.setOrganizationName(APP_IDENTITY.org_name)
        app.setApplicationName(APP_IDENTITY.app_name)
    settings = QSettings(APP_IDENTITY.org_name, APP_IDENTITY.app_name)
    for key in (
        "ui.window_geometry",
        "ui.window_is_maximized",
        "ui.capture_splitter_sizes",
        "ui.editor_splitter_sizes",
    ):
        settings.remove(key)
    settings.sync()


def test_window_starts_maximized_when_no_saved_state(qtbot: QtBot) -> None:
    _clear_window_state_settings()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert bool(window.windowState() & Qt.WindowState.WindowMaximized)
    _clear_window_state_settings()


def test_capture_and_editor_splitters_are_non_collapsible(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert not window.capture_splitter.childrenCollapsible()
    assert not window.capture_splitter.isCollapsible(0)
    assert not window.capture_splitter.isCollapsible(1)
    assert not window.editor_splitter.childrenCollapsible()
    assert not window.editor_splitter.isCollapsible(0)
    assert not window.editor_splitter.isCollapsible(1)


def test_default_splitter_right_pane_width_is_300_without_saved_state(qtbot: QtBot) -> None:
    _clear_window_state_settings()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.showNormal()
    window._load_runtime_settings()
    qtbot.wait(80)

    capture_sizes = window.capture_splitter.sizes()
    assert abs(capture_sizes[1] - 300) <= 40
    window.tabs.setCurrentIndex(1)
    qtbot.wait(80)
    editor_sizes = window.editor_splitter.sizes()
    assert abs(editor_sizes[1] - 300) <= 40
    _clear_window_state_settings()


def test_window_geometry_and_splitter_sizes_restore(qtbot: QtBot) -> None:
    _clear_window_state_settings()
    first = MainWindow()
    qtbot.addWidget(first)
    first.show()
    first.showNormal()
    first.setGeometry(80, 90, 1230, 760)
    first.capture_splitter.setSizes([620, 280])
    first.editor_splitter.setSizes([940, 260])
    first._persist_window_state_snapshot()
    first._persist_splitter_sizes()
    first.close()
    settings = QSettings(APP_IDENTITY.org_name, APP_IDENTITY.app_name)
    saved_capture = settings.value("ui.capture_splitter_sizes")
    saved_editor = settings.value("ui.editor_splitter_sizes")
    assert saved_capture is not None
    assert saved_editor is not None

    second = MainWindow()
    qtbot.addWidget(second)
    second.show()
    second.showNormal()
    second._load_runtime_settings()
    geometry = second.geometry()

    assert not second.isMaximized()
    assert geometry.width() >= 1120
    assert abs(geometry.height() - 760) <= 24
    capture_sizes = second.capture_splitter.sizes()
    editor_sizes = second.editor_splitter.sizes()
    assert len(second._int_list_setting("ui.capture_splitter_sizes")) >= 2
    assert len(second._int_list_setting("ui.editor_splitter_sizes")) >= 2
    assert all(size > 0 for size in capture_sizes)
    assert all(size > 0 for size in editor_sizes)
    _clear_window_state_settings()


def test_view_reset_action_restores_default_splitters(qtbot: QtBot) -> None:
    _clear_window_state_settings()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.showNormal()
    window.capture_splitter.setSizes([200, 900])
    window.editor_splitter.setSizes([300, 900])
    window._persist_window_state_snapshot()

    window._reset_view_state()
    qtbot.wait(80)

    capture_sizes = window.capture_splitter.sizes()
    assert capture_sizes[0] > 0 and capture_sizes[1] > 0
    assert capture_sizes != [200, 900]
    assert abs(capture_sizes[1] - 300) <= 40
    window.tabs.setCurrentIndex(1)
    qtbot.wait(80)
    editor_sizes = window.editor_splitter.sizes()
    assert editor_sizes[0] > 0 and editor_sizes[1] > 0
    assert editor_sizes != [300, 900]
    assert abs(editor_sizes[1] - 300) <= 40
    assert "view reset to defaults" in window.status_label.text().lower()
    _clear_window_state_settings()


def test_export_xlsx_setting_persists_and_defaults_false(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._settings = QSettings(
        "web-pagez-to-pdf-tests",
        "test_export_xlsx_setting_persists_and_defaults_false",
    )
    window._settings.clear()
    window._settings.sync()


def test_export_open_after_and_preview_debounce_settings_persist(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._settings = QSettings(
        "web-pagez-to-pdf-tests",
        "test_export_open_after_and_preview_debounce_settings_persist",
    )
    window._settings.clear()
    window._settings.sync()

    window._settings.setValue("export.open_after_export", False)
    window._settings.setValue("editor.preview_debounce_ms", 0)
    window._settings.sync()
    window._load_runtime_settings()
    assert not window.open_after_export_checkbox.isChecked()
    assert window._editor_preview_debounce_ms == 0

    window._settings.setValue("export.open_after_export", True)
    window._settings.setValue("editor.preview_debounce_ms", 9999)
    window._settings.sync()
    window._load_runtime_settings()
    assert window.open_after_export_checkbox.isChecked()
    assert window._editor_preview_debounce_ms == 2000

    payload = window._collect_settings_payload()
    assert bool(payload["export.open_after_export"]) is True
    assert int(payload["editor.preview_debounce_ms"]) == 2000

    window._settings.clear()
    window._settings.sync()


def test_editor_preview_debounce_applies_transform_after_delay(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._settings.setValue("editor.preview_debounce_ms", 180)
    window._settings.sync()
    window._load_runtime_settings()
    window._add_capture(
        image=Image.new("RGB", (300, 500), "white"),
        title="debounce",
        source_hwnd=None,
        frame_count=1,
    )
    item = window._current_item()
    assert item is not None
    edits = window._session_for_item(item.item_id)

    window.zoom_spin.setValue(125)
    window.rotate_spin.setValue(3)
    assert edits.get_operation("scale") is None
    assert edits.get_operation("rotate") is None

    qtbot.waitUntil(lambda: edits.get_operation("scale") is not None, timeout=1000)
    assert int(edits.get_operation("scale").params.get("percent", 0)) == 125  # type: ignore[union-attr]
    assert int(edits.get_operation("rotate").params.get("degrees", 0)) == 3  # type: ignore[union-attr]


def test_editor_preview_debounce_zero_applies_immediately(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._settings.setValue("editor.preview_debounce_ms", 0)
    window._settings.sync()
    window._load_runtime_settings()
    window._add_capture(
        image=Image.new("RGB", (300, 500), "white"),
        title="debounce-zero",
        source_hwnd=None,
        frame_count=1,
    )
    item = window._current_item()
    assert item is not None
    edits = window._session_for_item(item.item_id)

    window.zoom_spin.setValue(130)
    op = edits.get_operation("scale")
    assert op is not None
    assert int(op.params.get("percent", 0)) == 130

    window._settings.remove("export.xlsx")
    window._settings.sync()
    window._load_runtime_settings()
    assert not window.xlsx_checkbox.isChecked()

    window.xlsx_checkbox.setChecked(True)
    payload = window._collect_settings_payload()
    assert bool(payload["export.xlsx"]) is True

    window._settings.setValue("export.xlsx", True)
    window._settings.sync()
    window._load_runtime_settings()
    assert window.xlsx_checkbox.isChecked()
    payload_after_apply = window._collect_settings_payload()
    assert bool(payload_after_apply["export.xlsx"]) is True

    window._settings.clear()
    window._settings.sync()


def test_pan_tool_drag_moves_manual_split_markers(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (420, 1200), "white"),
        title="split-pan-drag",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()
    window.split_spin.setValue(210)
    window._add_split_marker()
    assert 210 in window._split_markers()
    window.pan_tool_button.click()
    window.editor_canvas.focus_on_y(235.0)
    qtbot.wait(50)

    start_y = window.editor_canvas.mapFromScene(QPointF(0.0, 210.0)).y()
    end_y = window.editor_canvas.mapFromScene(QPointF(0.0, 260.0)).y()
    start = QPoint(window.editor_canvas.viewport().width() - 8, int(start_y))
    end = QPoint(window.editor_canvas.viewport().width() - 8, int(end_y))
    qtbot.mousePress(window.editor_canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(window.editor_canvas.viewport(), pos=end)
    qtbot.mouseRelease(window.editor_canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    qtbot.waitUntil(lambda: any(marker != 210 for marker in window._split_markers()), timeout=1000)

    assert 210 not in window._split_markers()
    assert len(window._split_markers()) == 1
    assert window._split_markers()[0] > 210


def test_split_marker_drag_is_disabled_for_non_pan_tools(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (420, 1200), "white"),
        title="split-non-pan",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()
    window.split_spin.setValue(220)
    window._add_split_marker()
    assert 220 in window._split_markers()

    window.rect_crop_tool_button.click()
    start = window.editor_canvas.mapFromScene(QPointF(20.0, 220.0))
    end = window.editor_canvas.mapFromScene(QPointF(20.0, 280.0))
    qtbot.mousePress(window.editor_canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(window.editor_canvas.viewport(), pos=end)
    qtbot.mouseRelease(window.editor_canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)

    assert 220 in window._split_markers()
    assert 280 not in window._split_markers()


def test_crop_snap_rect_vertical_and_free_with_shift_override(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=_line_cue_image(),
        title="snap-cues",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()
    canvas = window.editor_canvas

    rect_snapped = canvas._snap_scene_point(
        QPointF(116.0, 77.0),
        tool="crop_rect",
        modifiers=Qt.KeyboardModifier.NoModifier,
    )
    assert abs(rect_snapped.x() - 120.0) <= 3.0
    assert abs(rect_snapped.y() - 80.0) <= 3.0
    rect_unsnapped = canvas._snap_scene_point(
        QPointF(116.0, 77.0),
        tool="crop_rect",
        modifiers=Qt.KeyboardModifier.ShiftModifier,
    )
    assert abs(rect_unsnapped.x() - 116.0) <= 0.6
    assert abs(rect_unsnapped.y() - 77.0) <= 0.6

    vertical_snapped = canvas._snap_scene_point(
        QPointF(116.0, 77.0),
        tool="crop_vertical_band",
        modifiers=Qt.KeyboardModifier.NoModifier,
    )
    assert abs(vertical_snapped.x() - 120.0) <= 3.0
    assert abs(vertical_snapped.y() - 77.0) <= 0.6
    vertical_unsnapped = canvas._snap_scene_point(
        QPointF(116.0, 77.0),
        tool="crop_vertical_band",
        modifiers=Qt.KeyboardModifier.ShiftModifier,
    )
    assert abs(vertical_unsnapped.x() - 116.0) <= 0.6
    assert abs(vertical_unsnapped.y() - 77.0) <= 0.6

    free_snapped = canvas._snap_scene_point(
        QPointF(116.0, 77.0),
        tool="crop_free",
        modifiers=Qt.KeyboardModifier.NoModifier,
    )
    assert abs(free_snapped.x() - 120.0) <= 3.0
    assert abs(free_snapped.y() - 80.0) <= 3.0
    free_unsnapped = canvas._snap_scene_point(
        QPointF(116.0, 77.0),
        tool="crop_free",
        modifiers=Qt.KeyboardModifier.ShiftModifier,
    )
    assert abs(free_unsnapped.x() - 116.0) <= 0.6
    assert abs(free_unsnapped.y() - 77.0) <= 0.6


def test_crosshair_magnifier_visible_only_for_crop_tools(qtbot: QtBot, tmp_path: Path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (420, 1200), "white"),
        title="magnifier-tool-visibility",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)
    window._refresh_preview()

    window.pan_tool_button.click()
    assert not window.editor_canvas._magnifier_enabled

    window.rect_crop_tool_button.click()
    assert window.editor_canvas._magnifier_enabled

    window.vertical_crop_tool_button.click()
    assert window.editor_canvas._magnifier_enabled

    window.free_crop_tool_button.click()
    assert window.editor_canvas._magnifier_enabled

    window.redact_tool_button.click()
    assert not window.editor_canvas._magnifier_enabled


def test_crosshair_magnifier_state_persists_across_refresh_and_runtime_reload(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.output_input.setText(str(tmp_path))
    window._add_capture(
        image=Image.new("RGB", (420, 1200), "white"),
        title="magnifier-refresh",
        source_hwnd=None,
        frame_count=1,
    )
    window.queue_list.setCurrentRow(0)

    window.rect_crop_tool_button.click()
    window._refresh_preview()
    assert window.editor_canvas._magnifier_enabled

    window.pan_tool_button.click()
    window._refresh_preview()
    assert not window.editor_canvas._magnifier_enabled

    window._load_runtime_settings()
    assert not window.editor_canvas._magnifier_enabled


def test_interactive_controls_have_tooltips(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    controls = [
        window.pick_list_button,
        window.capture_button,
        window.capture_full_button,
        window.stop_button,
        window.import_button,
        window.queue_list,
        window.max_pages_spin,
        window.capture_delay_spin,
        window.capture_backend_combo,
        window.capture_scroll_mode_combo,
        window.wizard_apply_queue_checkbox,
        window.wizard_auto_vertical_border_crop_button,
        window.wizard_remove_scrollbar_button,
        window.wizard_remove_border_button,
        window.wizard_undo_button,
        window.wizard_redo_button,
        window.editor_canvas,
        window.zoom_fit_width_button,
        window.editor_view_zoom_spin,
        window.pan_tool_button,
        window.rect_crop_tool_button,
        window.zoom_spin,
        window.paper_combo,
        window.margin_top_spin,
        window.blank_spin,
        window.header_input,
        window.footer_input,
        window.editor_overlay_toggle,
        window.thumbnail_zoom_slider,
        window.page_preview_list,
        window.split_spin,
        window.add_split_button,
        window.split_list,
        window.remove_split_button,
        window.combine_checkbox,
        window.pdf_checkbox,
        window.tiff_checkbox,
        window.xlsx_checkbox,
        window.open_after_export_checkbox,
        window.docx_mode_combo,
        window.base_input,
        window.output_input,
        window.export_button,
    ]
    missing = [widget.objectName() or widget.__class__.__name__ for widget in controls if not widget.toolTip().strip()]
    assert not missing
