"""Module entry point for ``python -m web_pagez_to_pdf``."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication
from threep_commons.paths import configure_qsettings

from .constants import APP_IDENTITY
from .main_window import MainWindow


def main() -> int:
    """Launch the desktop application main window."""

    configure_qsettings(APP_IDENTITY)
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_IDENTITY.org_name)
    app.setApplicationName(APP_IDENTITY.app_name)
    app.setApplicationDisplayName(APP_IDENTITY.display_name)
    window = MainWindow()
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
