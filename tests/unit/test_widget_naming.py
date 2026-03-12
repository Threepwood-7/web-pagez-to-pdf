from __future__ import annotations

from web_pagez_to_pdf import widget_naming
from threep_commons.qt.widget_identity import object_name_for_id


def test_widget_id_contracts() -> None:
    assert widget_naming.window_widget_id("main") == "window:main"
    assert (
        widget_naming.control_widget_id("main", "export_pdf")
        == "window:main:control:export_pdf"
    )


def test_object_name_for_id_sanitizes_non_identifier_chars() -> None:
    assert object_name_for_id("window:main:control:url_input") == "window_main_control_url_input"
