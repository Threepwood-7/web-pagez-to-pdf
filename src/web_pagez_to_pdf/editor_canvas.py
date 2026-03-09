"""Interactive editor canvas for in-tab image editing tools."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainterPath, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)


class EditorCanvas(QGraphicsView):
    """Zoom/pan image canvas with rectangle and free-form selection tools."""

    rect_drawn = Signal(str, object)
    free_crop_drawn = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)

        self._tool = "pan"
        self._drag_origin: QPointF | None = None
        self._rect_item: QGraphicsRectItem | None = None
        self._free_points: list[QPointF] = []
        self._free_path_item: QGraphicsPathItem | None = None
        self._zoom_mode = "fit_width"
        self._manual_zoom_percent = 100

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

    def set_image(self, pixmap: QPixmap) -> None:
        """Load pixmap and reset camera fit."""

        self._pixmap_item.setPixmap(pixmap)
        rect = QRectF(pixmap.rect())
        self._scene.setSceneRect(rect)
        self._apply_zoom()
        self._clear_overlay()

    def set_zoom_mode(self, mode: str, *, manual_percent: int | None = None) -> None:
        """Switch zoom mode and redraw view scaling."""

        normalized = str(mode or "").strip().lower()
        if normalized not in {"fit_height", "fit_width", "manual"}:
            normalized = "fit_width"
        self._zoom_mode = normalized
        if manual_percent is not None:
            self._manual_zoom_percent = max(10, min(400, int(manual_percent)))
        self._apply_zoom()

    def adjust_manual_zoom(self, delta_percent: int) -> None:
        """Adjust manual zoom by a relative percent step."""

        if self._zoom_mode != "manual":
            self._manual_zoom_percent = 100
        self.set_zoom_mode("manual", manual_percent=self._manual_zoom_percent + int(delta_percent))

    def zoom_label_text(self) -> str:
        """Human-readable zoom label for side controls."""

        if self._zoom_mode == "fit_height":
            return "Fit Height"
        if self._zoom_mode == "fit_width":
            return "Fit Width"
        return f"{self._manual_zoom_percent}%"

    def set_tool(self, tool: str) -> None:
        """Set active editing mode."""

        self._tool = tool.strip().lower()
        if self._tool == "pan":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._clear_overlay()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self.adjust_manual_zoom(10 if delta > 0 else -10)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._tool == "pan":
            super().mousePressEvent(event)
            return

        scene_point = self.mapToScene(event.position().toPoint())
        if self._tool in {"crop_rect", "crop_vertical_band", "redact"}:
            self._drag_origin = scene_point
            self._clear_overlay()
            self._rect_item = QGraphicsRectItem(QRectF(scene_point, scene_point))
            pen_color = Qt.GlobalColor.green if self._tool != "redact" else Qt.GlobalColor.red
            self._rect_item.setPen(QPen(pen_color, 2))
            self._scene.addItem(self._rect_item)
            return
        if self._tool == "crop_free":
            self._free_points.append(scene_point)
            self._refresh_free_path()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None and self._rect_item is not None:
            scene_point = self.mapToScene(event.position().toPoint())
            self._rect_item.setRect(QRectF(self._drag_origin, scene_point).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._drag_origin is not None
            and self._rect_item is not None
        ):
            rect = self._rect_item.rect().normalized()
            tool = self._tool
            self._drag_origin = None
            self._clear_overlay()
            if rect.width() >= 3 and rect.height() >= 3:
                self.rect_drawn.emit(tool, rect)
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._tool == "crop_free" and len(self._free_points) >= 3:
            points = [(point.x(), point.y()) for point in self._free_points]
            self._free_points = []
            self._clear_overlay()
            self.free_crop_drawn.emit(points)
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self._zoom_mode in {"fit_height", "fit_width"}:
            self._apply_zoom()
        super().resizeEvent(event)

    def _clear_overlay(self) -> None:
        if self._rect_item is not None:
            self._scene.removeItem(self._rect_item)
            self._rect_item = None
        if self._free_path_item is not None:
            self._scene.removeItem(self._free_path_item)
            self._free_path_item = None
        self._free_points = []

    def _refresh_free_path(self) -> None:
        if self._free_path_item is not None:
            self._scene.removeItem(self._free_path_item)
            self._free_path_item = None
        if len(self._free_points) < 2:
            return
        path = QPainterPath(self._free_points[0])
        for point in self._free_points[1:]:
            path.lineTo(point)
        self._free_path_item = QGraphicsPathItem(path)
        self._free_path_item.setPen(QPen(Qt.GlobalColor.cyan, 2))
        self._scene.addItem(self._free_path_item)

    def _apply_zoom(self) -> None:
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            self.resetTransform()
            return
        self.resetTransform()
        if self._zoom_mode == "fit_height":
            viewport_height = max(1, self.viewport().height() - 4)
            factor = viewport_height / max(1, pixmap.height())
        elif self._zoom_mode == "fit_width":
            viewport_width = max(1, self.viewport().width() - 4)
            factor = viewport_width / max(1, pixmap.width())
        else:
            factor = max(0.1, float(self._manual_zoom_percent) / 100.0)
        self.scale(factor, factor)
        self.centerOn(self._pixmap_item)
