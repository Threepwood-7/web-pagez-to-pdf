"""Window-target selection widgets: picker dialog and crosshair overlay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .capture_service import WindowInfo


@dataclass(slots=True)
class PickedWindow:
    """Selected window metadata."""

    hwnd: int
    label: str
    title: str = ""


class WindowPickerDialog(QDialog):
    """Simple top-level visible-window list picker."""

    def __init__(
        self, windows: list[WindowInfo], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._windows = windows
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("Select Capture Target Window")
        self.resize(680, 420)
        layout = QVBoxLayout(self)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for win in self._windows:
            item = QListWidgetItem(win.label)
            item.setData(Qt.ItemDataRole.UserRole, int(win.hwnd))
            self._list.addItem(item)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_window(self) -> PickedWindow | None:
        """Return selected item window metadata."""

        current = self._list.currentItem()
        if current is None:
            return None
        hwnd_data = current.data(Qt.ItemDataRole.UserRole)
        if hwnd_data is None:
            return None
        return PickedWindow(hwnd=int(hwnd_data), label=current.text())


class CrosshairPickerOverlay(QWidget):
    """Fullscreen overlay used to pick a target window by mouse point."""

    pick_requested = Signal(int, int)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def show_fullscreen_on_cursor_screen(self) -> None:
        """Show overlay on current cursor screen to capture point selection."""

        screen = self.screen()
        if screen is None and self.windowHandle() is not None:
            screen = self.windowHandle().screen()
        if screen is None:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.show()

    def mousePressEvent(self, event) -> None:
        point = event.globalPosition().toPoint()
        self.pick_requested.emit(point.x(), point.y())
        self.close()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 60))
        pen = QPen(QColor(255, 80, 80, 220))
        pen.setWidth(2)
        painter.setPen(pen)
        center = self.mapFromGlobal(QCursor.pos())
        painter.drawLine(center.x() - 20, center.y(), center.x() + 20, center.y())
        painter.drawLine(center.x(), center.y() - 20, center.x(), center.y() + 20)
        super().paintEvent(event)
