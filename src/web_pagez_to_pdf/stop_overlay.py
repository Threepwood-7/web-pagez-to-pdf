"""Always-on-top hover-to-stop overlay for long-running capture loops."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class HoverStopOverlay(QWidget):
    """Tiny top-right overlay that triggers stop when hovered by mouse."""

    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(220, 64)

        container = QWidget(self)
        container.setObjectName("stop_overlay_container")
        container.setGeometry(0, 0, self.width(), self.height())
        container.setStyleSheet(
            """
            QWidget#stop_overlay_container {
                background-color: rgba(171, 27, 32, 230);
                border: 1px solid rgba(255, 255, 255, 130);
                border-radius: 10px;
            }
            """
        )

        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        label = QLabel("Hover Here To Stop")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: white; font-weight: 700;")
        layout.addWidget(label)

        hint = QLabel("Move mouse over this badge")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: rgba(255, 255, 255, 220); font-size: 11px;")
        layout.addWidget(hint)

    def show_top_right(self) -> None:
        """Show overlay pinned to top-right corner of the active screen."""

        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            margin = 12
            x_pos = area.right() - self.width() - margin
            y_pos = area.top() + margin
            self.move(x_pos, y_pos)
        self.show()

    def enterEvent(self, event) -> None:
        self.stop_requested.emit()
        super().enterEvent(event)
