"""Module entry point for ``python -m web_pagez_to_pdf``."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from threep_commons.logging import setup_logging_from_identity
from threep_commons.paths import configure_qsettings

from .constants import APP_IDENTITY
from .main_window import MainWindow
from .scroll_capture import CAPTURE_LOGGER_NAME, normalize_capture_log_level


def main() -> int:
    """Launch the desktop application main window."""

    configure_qsettings(APP_IDENTITY)
    settings = QSettings()
    configured_level = normalize_capture_log_level(str(settings.value("capture.log_level", "INFO")))
    log_path = setup_logging_from_identity(
        APP_IDENTITY,
        level=logging.INFO,
        console=True,
    )
    logging.getLogger(CAPTURE_LOGGER_NAME).setLevel(
        logging.DEBUG if configured_level == "DEBUG" else logging.INFO
    )
    logging.getLogger("web_pagez_to_pdf").info(
        "Logging initialized path=%s capture_level=%s",
        log_path,
        configured_level,
    )
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_IDENTITY.org_name)
    app.setApplicationName(APP_IDENTITY.app_name)
    app.setApplicationDisplayName(APP_IDENTITY.display_name)
    window = MainWindow()
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
