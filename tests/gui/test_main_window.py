from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

from web_pagez_to_pdf.main_window import MainWindow

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


def test_main_window_widget_identity_contract(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.property("widget_id") == "window:main"
    assert window.property("widget_alias") == "window"

    assert window.url_input.property("widget_id") == "window:main:control:url_input"
    assert window.url_input.property("widget_alias") == "url_input"

    assert (
        window.output_path_input.property("widget_id")
        == "window:main:control:output_path_input"
    )
    assert window.output_path_input.property("widget_alias") == "output_path_input"

    assert window.browse_button.property("widget_id") == "window:main:control:browse_output"
    assert window.browse_button.property("widget_alias") == "browse_output"

    assert window.export_button.property("widget_id") == "window:main:control:export_pdf"
    assert window.export_button.property("widget_alias") == "export_pdf"

    assert window.status_label.property("widget_id") == "window:main:control:status_text"
    assert window.status_label.property("widget_alias") == "status_text"


def test_export_button_enabled_only_for_valid_inputs(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert not window.export_button.isEnabled()

    window.url_input.setText("ftp://example.com")
    window.output_path_input.setText("C:/tmp/page.pdf")
    assert not window.export_button.isEnabled()

    window.url_input.setText("https://example.com")
    assert window.export_button.isEnabled()

    window.output_path_input.setText("")
    assert not window.export_button.isEnabled()


def test_export_placeholder_is_non_blocking(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window.url_input.setText("https://example.com/page")
    window.output_path_input.setText("C:/tmp/page.pdf")
    assert window.export_button.isEnabled()

    qtbot.mouseClick(window.export_button, Qt.MouseButton.LeftButton)

    assert window.last_export_request == ("https://example.com/page", "C:/tmp/page.pdf")
    assert "not implemented yet" in window.status_label.text().lower()
