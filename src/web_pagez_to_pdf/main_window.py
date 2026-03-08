"""Main window for second-last-window screenshot capture."""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from threep_commons.paths import resolve_app_data_dir

from . import widget_naming
from .capture_service import WindowCaptureService
from .constants import APP_DISPLAY_NAME, APP_IDENTITY

if TYPE_CHECKING:
    from pathlib import Path


class MainWindow(QMainWindow):
    """Capture and open PNG screenshots of the previously active window."""

    def __init__(self) -> None:
        super().__init__()
        self.window_id = "main"
        self.last_capture_path: Path | None = None
        self._capture_service = WindowCaptureService()
        self._build_ui()
        self._bind_events()
        self._history_timer = QTimer(self)
        self._history_timer.setInterval(150)
        self._history_timer.timeout.connect(self._capture_service.record_foreground_window)
        self._history_timer.start()
        self._capture_service.record_foreground_window()

    def _build_ui(self) -> None:
        self._assign_widget_identity(self, widget_naming.window_widget_id(self.window_id), "window")
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(760, 260)

        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        self.instructions_label = QLabel(
            "Use the button or Ctrl+Shift+S to capture the second last active window as PNG."
        )
        self._assign_control_identity(self.instructions_label, "instructions", "instructions")
        self.instructions_label.setWordWrap(True)

        self.capture_button = QPushButton("Capture Previous Window")
        self._assign_control_identity(self.capture_button, "capture_previous_window", "capture_previous_window")

        self.shortcut_hint_label = QLabel("Shortcut: Ctrl+Shift+S")
        self._assign_control_identity(self.shortcut_hint_label, "shortcut_hint", "shortcut_hint")
        self.shortcut_hint_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.last_capture_label = QLabel("Last capture: none")
        self._assign_control_identity(self.last_capture_label, "last_capture_path", "last_capture_path")
        self.last_capture_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.last_capture_label.setWordWrap(True)

        self.status_label = QLabel("Ready.")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._assign_control_identity(self.status_label, "status_text", "status_text")

        root_layout.addWidget(self.instructions_label, stretch=0)
        root_layout.addWidget(self.capture_button, stretch=0)
        root_layout.addWidget(self.shortcut_hint_label, stretch=0)
        root_layout.addWidget(self.last_capture_label, stretch=0)
        root_layout.addWidget(self.status_label, stretch=0)
        root_layout.addStretch(1)
        self.setCentralWidget(root)

    def _bind_events(self) -> None:
        self.capture_button.clicked.connect(self._capture_second_last_window)
        self.capture_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self.capture_shortcut.activated.connect(self._capture_second_last_window)

    def _assign_control_identity(self, widget: QWidget, control: str, alias: str) -> None:
        widget_id = widget_naming.control_widget_id(self.window_id, control)
        self._assign_widget_identity(widget, widget_id, alias)

    @staticmethod
    def _assign_widget_identity(widget: QWidget, widget_id: str, alias: str) -> None:
        widget.setObjectName(widget_naming.object_name_for_id(widget_id))
        widget.setProperty("widget_id", widget_id)
        widget.setProperty("widget_alias", alias)

    def _capture_second_last_window(self) -> None:
        own_hwnd = int(self.winId())
        target_hwnd = self._capture_service.resolve_second_last_window(own_hwnd=own_hwnd)
        if target_hwnd is None:
            self.status_label.setText("No suitable previous window found to capture.")
            return

        pixmap = self._capture_service.capture_window(target_hwnd)
        if pixmap is None:
            self.status_label.setText("Failed to capture screenshot from previous window.")
            return

        png_path = self._next_capture_path()
        if not pixmap.save(str(png_path), "PNG"):
            self.status_label.setText("Failed to save screenshot PNG.")
            return

        self.last_capture_path = png_path
        self.last_capture_label.setText(f"Last capture: {png_path}")

        window_title = self._capture_service.window_title(target_hwnd)
        self.status_label.setText(f"Captured '{window_title}' to PNG and opening it.")

        try:
            os.startfile(str(png_path))
        except OSError as exc:
            self.status_label.setText(f"Saved PNG but failed to open viewer: {exc}")

    @staticmethod
    def _next_capture_path():
        capture_dir = resolve_app_data_dir(APP_IDENTITY) / "captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return capture_dir / f"window-capture-{timestamp}.png"
