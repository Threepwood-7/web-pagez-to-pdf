from __future__ import annotations

from web_pagez_to_pdf.settings_window import SettingsWindow


def test_settings_window_roundtrip_user_facing_values(qtbot) -> None:
    window = SettingsWindow()
    qtbot.addWidget(window)
    window.show()
    payload = {
        "capture.max_pages": 88,
        "capture.delay_ms": 420,
        "capture.backend_primary": "print_window",
        "capture.scroll_mode": "wheel_click",
        "capture.scroll_to_top_on_full": False,
        "capture.auto_trim_fixed_strips": False,
        "capture.frame_region": "full_window",
        "capture.wheel_injection_mode": "legacy_message_wheel",
        "capture.cursor_hold_mode": "restore_each_step",
        "capture.log_level": "DEBUG",
        "capture.include_mouse_cursor": True,
        "export.output_dir": "C:/tmp/out",
        "export.basename": "sheet",
        "export.combine_mode": False,
        "export.pdf": False,
        "export.paged_images": True,
        "export.long_image": True,
        "export.tiff": True,
        "export.docx": True,
        "export.pptx": True,
        "export.xlsx": True,
        "export.open_after_export": False,
        "export.docx_mode": "per_capture",
        "layout.paper_name": "LETTER",
        "layout.orientation": "landscape",
        "layout.margin_top_mm": 11.5,
        "layout.margin_bottom_mm": 12.5,
        "layout.margin_left_mm": 13.5,
        "layout.margin_right_mm": 14.5,
        "layout.gutter_mm": 3.0,
        "layout.blank_row_threshold": 200,
        "layout.search_window_px": 555,
        "layout.header_html": "<p><b>Head {page}</b></p>",
        "layout.footer_html": "<p><i>Foot {pages}</i></p>",
        "ui.editor_overlay_visible": False,
        "editor.preview_debounce_ms": 275,
        "ui.editor_adv_collapsed": False,
        "ui.export_adv_collapsed": True,
    }

    window.set_values(payload)
    values = window.values()

    assert int(values["capture.max_pages"]) == 88
    assert int(values["capture.delay_ms"]) == 420
    assert str(values["capture.backend_primary"]) == "print_window"
    assert str(values["capture.frame_region"]) == "full_window"
    assert bool(values["capture.include_mouse_cursor"]) is True
    assert str(values["export.docx_mode"]) == "per_capture"
    assert bool(values["export.open_after_export"]) is False
    assert str(values["layout.paper_name"]) == "LETTER"
    assert str(values["layout.orientation"]) == "landscape"
    assert abs(float(values["layout.margin_top_mm"]) - 11.5) < 0.01
    assert int(values["layout.blank_row_threshold"]) == 200
    assert int(values["layout.search_window_px"]) == 555
    assert "Head {page}" in str(values["layout.header_html"])
    assert "Foot {pages}" in str(values["layout.footer_html"])
    assert bool(values["ui.editor_overlay_visible"]) is False
    assert int(values["editor.preview_debounce_ms"]) == 275


def test_settings_window_hides_internal_view_state_keys(qtbot) -> None:
    window = SettingsWindow()
    qtbot.addWidget(window)
    window.show()
    values = window.values()

    assert "ui.window_geometry" not in values
    assert "ui.window_is_maximized" not in values
    assert "ui.capture_splitter_sizes" not in values
    assert "ui.editor_splitter_sizes" not in values
