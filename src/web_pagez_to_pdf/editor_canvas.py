"""Interactive editor canvas for in-tab image editing tools."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
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
    split_marker_added = Signal(int)
    split_marker_moved = Signal(int, int)
    split_marker_removed = Signal(int)

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
        self._overlay_visible = True
        self._manual_markers_px: list[int] = []
        self._page_slices_px: list[tuple[int, int]] = []
        self._printable_width_px = 0
        self._scene_padding_px = 120
        self._ruler_size_px = 26
        self._checker_step_px = 24
        self._split_drag_original: int | None = None
        self._split_drag_current: int | None = None

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

    def set_image(self, pixmap: QPixmap) -> None:
        """Load pixmap and reset camera fit."""

        self._pixmap_item.setPixmap(pixmap)
        rect = QRectF(pixmap.rect())
        if pixmap.isNull():
            self._scene.setSceneRect(QRectF())
            self._manual_markers_px = []
            self._page_slices_px = []
            self._printable_width_px = 0
        else:
            pad = float(self._scene_padding_px)
            self._scene.setSceneRect(rect.adjusted(-pad, -pad, pad, pad))
        self._apply_zoom()
        self._clear_overlay()
        self.viewport().update()

    def set_overlay_visibility(self, visible: bool) -> None:
        """Enable/disable visual page and split guides."""

        self._overlay_visible = bool(visible)
        self.viewport().update()

    def overlay_visible(self) -> bool:
        """Return current visual overlay visibility."""

        return self._overlay_visible

    def set_page_overlays(
        self,
        manual_markers_px: list[int],
        page_slices_px: list[tuple[int, int]],
        *,
        printable_width_px: int,
    ) -> None:
        """Set page and marker overlays using image-space pixel coordinates."""

        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            self._manual_markers_px = []
            self._page_slices_px = []
            self._printable_width_px = 0
            self.viewport().update()
            return
        height = max(1, pixmap.height())
        self._manual_markers_px = sorted(
            {int(value) for value in manual_markers_px if 0 < int(value) < height}
        )
        normalized_slices: list[tuple[int, int]] = []
        for top, bottom in page_slices_px:
            top_px = max(0, min(height - 1, int(top)))
            bottom_px = max(top_px + 1, min(height, int(bottom)))
            normalized_slices.append((top_px, bottom_px))
        self._page_slices_px = normalized_slices
        self._printable_width_px = max(0, min(int(printable_width_px), pixmap.width()))
        self.viewport().update()

    def focus_on_y(self, y_pos: float) -> None:
        """Center viewport on the provided image-space Y coordinate."""

        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return
        clamped_y = max(0.0, min(float(pixmap.height() - 1), float(y_pos)))
        self.centerOn(QPointF(float(pixmap.width()) / 2.0, clamped_y))

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
        self._split_drag_original = None
        self._split_drag_current = None
        self._clear_overlay()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self.adjust_manual_zoom(10 if delta > 0 else -10)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._tool == "split_edit":
            scene_point = self.mapToScene(event.position().toPoint())
            if event.button() == Qt.MouseButton.RightButton:
                marker = self._nearest_marker(scene_point.y())
                if marker is not None:
                    self._manual_markers_px = [value for value in self._manual_markers_px if value != marker]
                    self.split_marker_removed.emit(marker)
                    self.viewport().update()
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                marker = self._nearest_marker(scene_point.y())
                if marker is None:
                    clamped = self._clamp_marker(scene_point.y())
                    if clamped is not None and clamped not in self._manual_markers_px:
                        self._manual_markers_px = sorted({*self._manual_markers_px, clamped})
                        self.split_marker_added.emit(clamped)
                        self.viewport().update()
                    event.accept()
                    return
                self._split_drag_original = marker
                self._split_drag_current = marker
                event.accept()
                return
            event.ignore()
            return

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
        if self._tool == "split_edit" and self._split_drag_original is not None:
            scene_point = self.mapToScene(event.position().toPoint())
            clamped = self._clamp_marker(scene_point.y())
            if clamped is None or clamped == self._split_drag_current:
                return
            self._replace_marker(self._split_drag_original, clamped)
            self._split_drag_current = clamped
            self.viewport().update()
            event.accept()
            return
        if self._drag_origin is not None and self._rect_item is not None:
            scene_point = self.mapToScene(event.position().toPoint())
            self._rect_item.setRect(QRectF(self._drag_origin, scene_point).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._tool == "split_edit" and event.button() == Qt.MouseButton.LeftButton:
            original = self._split_drag_original
            current = self._split_drag_current
            self._split_drag_original = None
            self._split_drag_current = None
            if original is not None and current is not None and original != current:
                self.split_marker_moved.emit(original, current)
                self.viewport().update()
            event.accept()
            return
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

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawBackground(painter, rect)
        if rect.isEmpty():
            return
        step = max(6, int(self._checker_step_px))
        light = QColor(245, 245, 245)
        dark = QColor(232, 232, 232)
        start_x = int(math.floor(rect.left() / step) * step)
        start_y = int(math.floor(rect.top() / step) * step)
        end_x = math.ceil(rect.right())
        end_y = math.ceil(rect.bottom())
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for x_pos in range(start_x, end_x + step, step):
            for y_pos in range(start_y, end_y + step, step):
                color = light if ((x_pos // step) + (y_pos // step)) % 2 == 0 else dark
                painter.setBrush(color)
                painter.drawRect(QRectF(float(x_pos), float(y_pos), float(step), float(step)))
        painter.restore()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawForeground(painter, rect)
        _unused = rect
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return
        width = float(pixmap.width())
        height = float(pixmap.height())
        image_rect = QRectF(0.0, 0.0, width, height)

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(35, 35, 35), 1))
        painter.drawRect(image_rect)
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
        painter.drawRect(image_rect.adjusted(1.0, 1.0, -1.0, -1.0))
        painter.restore()

        if not self._overlay_visible:
            return

        if 0 < self._printable_width_px < pixmap.width():
            print_w = float(self._printable_width_px)
            side = max(0.0, (width - print_w) / 2.0)
            left_x = side
            right_x = width - side
            painter.save()
            guide_pen = QPen(QColor(0, 100, 255, 180), 1, Qt.PenStyle.DashDotLine)
            painter.setPen(guide_pen)
            painter.drawLine(QPointF(left_x, 0.0), QPointF(left_x, height))
            painter.drawLine(QPointF(right_x, 0.0), QPointF(right_x, height))
            painter.restore()

        if self._page_slices_px:
            painter.save()
            page_pen = QPen(QColor(38, 132, 255, 220), 1, Qt.PenStyle.DashLine)
            painter.setPen(page_pen)
            for page_number, (top, bottom) in enumerate(self._page_slices_px, start=1):
                label_y = max(14.0, float(top) + 16.0)
                painter.drawText(QPointF(8.0, label_y), f"Page {page_number}")
                y_line = float(bottom)
                if y_line < 1.0 or y_line >= height:
                    continue
                painter.drawLine(QPointF(0.0, y_line), QPointF(width, y_line))
            painter.restore()

        if self._manual_markers_px:
            painter.save()
            marker_pen = QPen(QColor(255, 145, 0, 230), 1, Qt.PenStyle.DashDotDotLine)
            painter.setPen(marker_pen)
            for marker in self._manual_markers_px:
                y_line = float(marker)
                if y_line < 1.0 or y_line >= height:
                    continue
                painter.drawLine(QPointF(0.0, y_line), QPointF(width, y_line))
            painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        try:
            self._paint_rulers(painter)
        finally:
            painter.end()

    def _paint_rulers(self, painter: QPainter) -> None:
        viewport_rect = self.viewport().rect()
        if viewport_rect.isEmpty():
            return
        ruler_size = int(self._ruler_size_px)
        top_rect = QRect(0, 0, viewport_rect.width(), ruler_size)
        right_rect = QRect(
            max(0, viewport_rect.width() - ruler_size),
            0,
            ruler_size,
            viewport_rect.height(),
        )
        corner_rect = QRect(
            max(0, viewport_rect.width() - ruler_size),
            0,
            ruler_size,
            ruler_size,
        )

        painter.save()
        painter.fillRect(top_rect, QColor(242, 242, 242, 230))
        painter.fillRect(right_rect, QColor(242, 242, 242, 230))
        painter.fillRect(corner_rect, QColor(228, 228, 228, 240))
        painter.setPen(QPen(QColor(120, 120, 120, 220), 1))
        painter.drawLine(
            QPoint(0, ruler_size - 1),
            QPoint(viewport_rect.width(), ruler_size - 1),
        )
        painter.drawLine(
            QPoint(viewport_rect.width() - ruler_size, 0),
            QPoint(viewport_rect.width() - ruler_size, viewport_rect.height()),
        )
        painter.restore()

        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return
        self._paint_horizontal_ticks(painter, top_rect, pixmap.width())
        self._paint_vertical_ticks(painter, right_rect, pixmap.height())

    def _paint_horizontal_ticks(self, painter: QPainter, top_rect: QRect, image_width: int) -> None:
        visible_scene = self.mapToScene(self.viewport().rect()).boundingRect()
        start = max(0, int(math.floor(visible_scene.left() / 10.0) * 10))
        end = min(image_width, int(math.ceil(visible_scene.right() / 10.0) * 10))
        painter.save()
        painter.setPen(QPen(QColor(55, 55, 55), 1))
        for x_pos in range(start, end + 1, 10):
            view_x = round(self.mapFromScene(QPointF(float(x_pos), 0.0)).x())
            if view_x < 0 or view_x > top_rect.right():
                continue
            if x_pos % 100 == 0:
                tick_len = 12
            elif x_pos % 50 == 0:
                tick_len = 9
            else:
                tick_len = 5
            painter.drawLine(
                QPoint(view_x, top_rect.bottom()),
                QPoint(view_x, top_rect.bottom() - tick_len),
            )
            if x_pos % 100 == 0:
                painter.drawText(QPoint(view_x + 2, top_rect.top() + 12), str(x_pos))
        painter.restore()

    def _paint_vertical_ticks(self, painter: QPainter, right_rect: QRect, image_height: int) -> None:
        visible_scene = self.mapToScene(self.viewport().rect()).boundingRect()
        start = max(0, int(math.floor(visible_scene.top() / 10.0) * 10))
        end = min(image_height, int(math.ceil(visible_scene.bottom() / 10.0) * 10))
        painter.save()
        painter.setPen(QPen(QColor(55, 55, 55), 1))
        for y_pos in range(start, end + 1, 10):
            view_y = round(self.mapFromScene(QPointF(0.0, float(y_pos))).y())
            if view_y < 0 or view_y > right_rect.bottom():
                continue
            if y_pos % 100 == 0:
                tick_len = 12
            elif y_pos % 50 == 0:
                tick_len = 9
            else:
                tick_len = 5
            painter.drawLine(
                QPoint(right_rect.left(), view_y),
                QPoint(right_rect.left() + tick_len, view_y),
            )
            if y_pos % 100 == 0:
                painter.drawText(QPoint(right_rect.left() + 2, view_y - 2), str(y_pos))
        painter.restore()

    def _nearest_marker(self, y_pos: float) -> int | None:
        if not self._manual_markers_px:
            return None
        tolerance = self._split_hit_tolerance()
        nearest: int | None = None
        nearest_delta: float | None = None
        for marker in self._manual_markers_px:
            delta = abs(float(marker) - float(y_pos))
            if nearest_delta is None or delta < nearest_delta:
                nearest = marker
                nearest_delta = delta
        if nearest is None or nearest_delta is None or nearest_delta > tolerance:
            return None
        return nearest

    def _split_hit_tolerance(self) -> float:
        scale = abs(float(self.transform().m22()))
        if scale <= 0.0001:
            scale = 1.0
        return max(4.0, 8.0 / scale)

    def _clamp_marker(self, y_pos: float) -> int | None:
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return None
        if pixmap.height() <= 1:
            return None
        return int(max(1, min(pixmap.height() - 1, round(float(y_pos)))))

    def _replace_marker(self, old_value: int, new_value: int) -> None:
        markers = [value for value in self._manual_markers_px if int(value) != int(old_value)]
        markers.append(int(new_value))
        self._manual_markers_px = sorted({int(value) for value in markers if int(value) > 0})

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
            viewport_height = max(1, self.viewport().height() - 4 - self._ruler_size_px)
            factor = viewport_height / max(1, pixmap.height())
        elif self._zoom_mode == "fit_width":
            viewport_width = max(1, self.viewport().width() - 4 - self._ruler_size_px)
            factor = viewport_width / max(1, pixmap.width())
        else:
            factor = max(0.1, float(self._manual_zoom_percent) / 100.0)
        self.scale(factor, factor)
        self.centerOn(self._pixmap_item)
