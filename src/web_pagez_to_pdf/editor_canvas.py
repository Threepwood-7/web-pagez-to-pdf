"""Interactive editor canvas for in-tab image editing tools."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygon,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
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
    hover_scene_position_changed = Signal(object)
    zoom_changed = Signal(str, int)

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
        self._split_action_mode = "none"
        self._split_drag_original: int | None = None
        self._split_drag_current: int | None = None
        self._hover_scene_point: QPointF | None = None
        self._hover_view_point: QPoint = QPoint()
        self._hover_inside_image = False
        self._magnifier_enabled = True
        self._magnifier_zoom = 12
        self._magnifier_size_px = 170
        self._hover_overlay_pixmap: QPixmap | None = None
        self._snap_edge_vertical: np.ndarray | None = None
        self._snap_edge_horizontal: np.ndarray | None = None
        self._snap_radius_px = 12
        self._snap_strength_threshold = 40.0

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)

    def set_image(self, pixmap: QPixmap) -> None:
        """Load pixmap and reset camera fit."""

        self._pixmap_item.setPixmap(pixmap)
        rect = QRectF(pixmap.rect())
        if pixmap.isNull():
            self._scene.setSceneRect(QRectF())
            self._manual_markers_px = []
            self._page_slices_px = []
            self._printable_width_px = 0
            self._hover_scene_point = None
            self._hover_inside_image = False
            self._snap_edge_vertical = None
            self._snap_edge_horizontal = None
        else:
            pad = float(self._scene_padding_px)
            self._scene.setSceneRect(rect.adjusted(-pad, -pad, pad, pad))
        self._rebuild_snap_cache(pixmap)
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

    def scroll_to_top(self) -> None:
        """Scroll both axes to their minimum values."""

        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().minimum())

    def set_zoom_mode(self, mode: str, *, manual_percent: int | None = None) -> None:
        """Switch zoom mode and redraw view scaling."""

        normalized = str(mode or "").strip().lower()
        if normalized not in {"fit_height", "fit_width", "manual"}:
            normalized = "fit_width"
        self._zoom_mode = normalized
        if manual_percent is not None:
            self._manual_zoom_percent = max(10, min(400, int(manual_percent)))
        self._apply_zoom()
        self.zoom_changed.emit(self._zoom_mode, int(self._manual_zoom_percent))

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

    def zoom_mode(self) -> str:
        """Return current zoom mode."""

        return self._zoom_mode

    def manual_zoom_percent(self) -> int:
        """Return current manual zoom percent."""

        return int(self._manual_zoom_percent)

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

    def set_split_action_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in {"none", "add", "remove"}:
            normalized = "none"
        self._split_action_mode = normalized
        self._split_drag_original = None
        self._split_drag_current = None

    def set_magnifier_state(self, *, enabled: bool, zoom_factor: int) -> None:
        """Configure floating magnifier visibility and scale."""

        self._magnifier_enabled = bool(enabled)
        self._magnifier_zoom = max(2, min(32, int(zoom_factor)))
        self.viewport().update()

    def sample_hover_zoom(self, *, zoom_factor: int, output_size: int = 220) -> QPixmap | None:
        """Return a crosshair patch around last hover point for quick-zoom panel."""

        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull() or self._hover_scene_point is None:
            return None
        return self._build_zoom_patch(
            pixmap,
            self._hover_scene_point,
            zoom_factor=max(2, int(zoom_factor)),
            output_size=max(48, int(output_size)),
        )

    def set_hover_overlay_pixmap(self, pixmap: QPixmap | None) -> None:
        """Set temporary full-view overlay shown above editor image."""

        self._hover_overlay_pixmap = pixmap
        self.viewport().update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self.adjust_manual_zoom(10 if delta > 0 else -10)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._update_hover_state(event.position().toPoint())

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._split_action_mode in {"add", "remove"}
        ):
            marker_y = self._split_action_target_y(event.position().toPoint())
            if marker_y is not None:
                if self._split_action_mode == "add":
                    self.split_marker_added.emit(int(marker_y))
                else:
                    self.split_marker_removed.emit(int(marker_y))
                event.accept()
                return

        if event.button() == Qt.MouseButton.LeftButton and self._tool == "pan":
            scene_point = self.mapToScene(event.position().toPoint())
            marker = self._nearest_marker(scene_point.y())
            if marker is not None and self._split_knob_hit(event.position().toPoint(), marker):
                self._split_drag_original = int(marker)
                self._split_drag_current = int(marker)
                event.accept()
                return

        if event.button() != Qt.MouseButton.LeftButton or self._tool == "pan":
            super().mousePressEvent(event)
            return

        scene_point = self.mapToScene(event.position().toPoint())
        if self._tool in {"crop_rect", "crop_vertical_band", "redact"}:
            if self._tool in {"crop_rect", "crop_vertical_band"}:
                scene_point = self._snap_scene_point(
                    scene_point,
                    tool=self._tool,
                    modifiers=event.modifiers(),
                )
            self._drag_origin = scene_point
            self._clear_overlay()
            self._rect_item = QGraphicsRectItem(QRectF(scene_point, scene_point))
            pen_color = Qt.GlobalColor.green if self._tool != "redact" else Qt.GlobalColor.red
            self._rect_item.setPen(QPen(pen_color, 2))
            self._scene.addItem(self._rect_item)
            return
        if self._tool == "crop_free":
            scene_point = self._snap_scene_point(
                scene_point,
                tool=self._tool,
                modifiers=event.modifiers(),
            )
            self._free_points.append(scene_point)
            self._refresh_free_path()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._update_hover_state(event.position().toPoint())
        if self._tool == "pan" and self._split_drag_original is not None:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._split_drag_original = None
                self._split_drag_current = None
                super().mouseMoveEvent(event)
                return
            scene_point = self.mapToScene(event.position().toPoint())
            clamped = self._clamp_marker(scene_point.y())
            if clamped is None or clamped == self._split_drag_current:
                return
            current_marker = self._split_drag_current
            if current_marker is None:
                return
            self._replace_marker(current_marker, clamped)
            self._split_drag_current = clamped
            self.viewport().update()
            event.accept()
            return
        if self._drag_origin is not None and self._rect_item is not None:
            scene_point = self.mapToScene(event.position().toPoint())
            if self._tool in {"crop_rect", "crop_vertical_band"}:
                scene_point = self._snap_scene_point(
                    scene_point,
                    tool=self._tool,
                    modifiers=event.modifiers(),
                )
            self._rect_item.setRect(QRectF(self._drag_origin, scene_point).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            self._tool == "pan"
            and event.button() == Qt.MouseButton.LeftButton
            and self._split_drag_original is not None
        ):
            original = self._split_drag_original
            current = self._split_drag_current
            self._split_drag_original = None
            self._split_drag_current = None
            if original is not None and current is not None and original != current:
                self.split_marker_moved.emit(int(original), int(current))
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

    def leaveEvent(self, event) -> None:  # noqa: N802
        if (
            self._tool == "pan"
            and self._split_drag_original is not None
            and not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        ):
            self._split_drag_original = None
            self._split_drag_current = None
        self._hover_inside_image = False
        self.hover_scene_position_changed.emit(
            {
                "inside": False,
                "x": int(self._hover_scene_point.x()) if self._hover_scene_point is not None else -1,
                "y": int(self._hover_scene_point.y()) if self._hover_scene_point is not None else -1,
            }
        )
        self.viewport().update()
        super().leaveEvent(event)

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

        if self._manual_markers_px:
            painter.save()
            marker_shadow_pen = QPen(QColor(40, 40, 40, 210), 3, Qt.PenStyle.SolidLine)
            marker_core_pen = QPen(QColor(255, 132, 0, 245), 2, Qt.PenStyle.SolidLine)
            for marker in self._manual_markers_px:
                y_line = float(marker)
                if y_line < 1.0 or y_line >= height:
                    continue
                painter.setPen(marker_shadow_pen)
                painter.drawLine(QPointF(0.0, y_line), QPointF(width, y_line))
                painter.setPen(marker_core_pen)
                painter.drawLine(QPointF(0.0, y_line), QPointF(width, y_line))
            painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        try:
            self._paint_rulers(painter)
            self._paint_main_hover_overlay(painter)
            self._paint_floating_magnifier(painter)
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
        self._paint_split_knobs(painter, right_rect)

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

    def _paint_split_knobs(self, painter: QPainter, right_rect: QRect) -> None:
        if not self._manual_markers_px:
            return
        painter.save()
        painter.setPen(QPen(QColor(64, 64, 64, 230), 1))
        painter.setBrush(QColor(255, 132, 0, 240))
        tri_height = 7
        tri_width = max(9, right_rect.width() - 9)
        for marker in self._manual_markers_px:
            view_y = round(self.mapFromScene(QPointF(0.0, float(marker))).y())
            if view_y < 0 or view_y > right_rect.bottom():
                continue
            tri_left = right_rect.left() + 2
            triangle = QPolygon(
                [
                    QPoint(tri_left, view_y),
                    QPoint(tri_left + tri_width, view_y - tri_height),
                    QPoint(tri_left + tri_width, view_y + tri_height),
                ]
            )
            painter.drawPolygon(triangle)
        painter.restore()

    def _split_action_target_y(self, view_pos: QPoint) -> int | None:
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull() or pixmap.height() <= 1:
            return None
        scene_point = self.mapToScene(view_pos)
        image_rect = QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height()))
        ruler_left = max(0, self.viewport().width() - int(self._ruler_size_px))
        in_ruler = int(view_pos.x()) >= int(ruler_left)
        if not in_ruler and not image_rect.contains(scene_point):
            return None
        return self._clamp_marker(scene_point.y())

    def _nearest_marker(self, y_pos: float) -> int | None:
        if not self._manual_markers_px:
            return None
        tolerance = self._split_hit_tolerance()
        nearest: int | None = None
        nearest_delta: float | None = None
        for marker in self._manual_markers_px:
            delta = abs(float(marker) - float(y_pos))
            if nearest_delta is None or delta < nearest_delta:
                nearest = int(marker)
                nearest_delta = delta
        if nearest is None or nearest_delta is None or nearest_delta > tolerance:
            return None
        return int(nearest)

    def _split_knob_hit(self, view_pos: QPoint, marker: int) -> bool:
        ruler_left = max(0, self.viewport().width() - int(self._ruler_size_px) - 16)
        if int(view_pos.x()) < int(ruler_left):
            return False
        marker_view_y = self.mapFromScene(QPointF(0.0, float(marker))).y()
        return abs(int(view_pos.y()) - int(marker_view_y)) <= 10

    def _split_hit_tolerance(self) -> float:
        scale = abs(float(self.transform().m22()))
        if scale <= 0.0001:
            scale = 1.0
        return max(4.0, 8.0 / scale)

    def _clamp_marker(self, y_pos: float) -> int | None:
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull() or pixmap.height() <= 1:
            return None
        return int(max(1, min(pixmap.height() - 1, round(float(y_pos)))))

    def _replace_marker(self, old_value: int, new_value: int) -> None:
        markers = [value for value in self._manual_markers_px if int(value) != int(old_value)]
        markers.append(int(new_value))
        self._manual_markers_px = sorted({int(value) for value in markers if int(value) > 0})

    def _update_hover_state(self, view_pos: QPoint) -> None:
        self._hover_view_point = QPoint(int(view_pos.x()), int(view_pos.y()))
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return
        scene_point = self.mapToScene(view_pos)
        image_rect = QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height()))
        if image_rect.contains(scene_point):
            x_pos = max(0.0, min(float(pixmap.width() - 1), float(scene_point.x())))
            y_pos = max(0.0, min(float(pixmap.height() - 1), float(scene_point.y())))
            self._hover_scene_point = QPointF(x_pos, y_pos)
            self._hover_inside_image = True
            self.hover_scene_position_changed.emit(
                {"inside": True, "x": round(x_pos), "y": round(y_pos)}
            )
        else:
            self._hover_inside_image = False
            self.hover_scene_position_changed.emit(
                {
                    "inside": False,
                    "x": int(self._hover_scene_point.x()) if self._hover_scene_point is not None else -1,
                    "y": int(self._hover_scene_point.y()) if self._hover_scene_point is not None else -1,
                }
            )
        self.viewport().update()

    def _rebuild_snap_cache(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            self._snap_edge_vertical = None
            self._snap_edge_horizontal = None
            return
        image = pixmap.toImage().convertToFormat(pixmap.toImage().Format.Format_Grayscale8)
        width = image.width()
        height = image.height()
        if width <= 1 or height <= 1:
            self._snap_edge_vertical = None
            self._snap_edge_horizontal = None
            return
        ptr = image.constBits()
        gray = np.frombuffer(ptr, dtype=np.uint8).reshape((height, image.bytesPerLine()))[:, :width]
        gray_f = gray.astype(np.float32)
        vertical = np.abs(np.diff(gray_f, axis=1, prepend=gray_f[:, :1]))
        horizontal = np.abs(np.diff(gray_f, axis=0, prepend=gray_f[:1, :]))
        self._snap_edge_vertical = vertical
        self._snap_edge_horizontal = horizontal

    def _snap_scene_point(
        self,
        scene_point: QPointF,
        *,
        tool: str,
        modifiers: Qt.KeyboardModifier | Qt.KeyboardModifiers,
    ) -> QPointF:
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return scene_point
        normalized_tool = str(tool or "").strip().lower()
        snap_x = normalized_tool in {"crop_rect", "crop_vertical_band", "crop_free"}
        snap_y = normalized_tool in {"crop_rect", "crop_free"}
        if not snap_x and not snap_y:
            return scene_point
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return scene_point
        x_pos = round(scene_point.x())
        y_pos = round(scene_point.y())
        if x_pos < 0 or y_pos < 0 or x_pos >= pixmap.width() or y_pos >= pixmap.height():
            return scene_point
        snapped_x = x_pos
        snapped_y = y_pos
        if snap_x:
            x_candidate = self._snap_axis_coordinate(
                center_axis=x_pos,
                fixed_axis=y_pos,
                strength_map=self._snap_edge_vertical,
                axis="x",
            )
            if x_candidate is not None:
                snapped_x = x_candidate
        if snap_y:
            y_candidate = self._snap_axis_coordinate(
                center_axis=y_pos,
                fixed_axis=x_pos,
                strength_map=self._snap_edge_horizontal,
                axis="y",
            )
            if y_candidate is not None:
                snapped_y = y_candidate
        return QPointF(float(snapped_x), float(snapped_y))

    def _snap_axis_coordinate(
        self,
        *,
        center_axis: int,
        fixed_axis: int,
        strength_map: np.ndarray | None,
        axis: str,
    ) -> int | None:
        if strength_map is None:
            return None
        radius = int(self._snap_radius_px)
        height, width = strength_map.shape[:2]
        if axis == "x":
            x0 = max(0, center_axis - radius)
            x1 = min(width, center_axis + radius + 1)
            y0 = max(0, fixed_axis - radius)
            y1 = min(height, fixed_axis + radius + 1)
            region = strength_map[y0:y1, x0:x1]
            if region.size == 0:
                return None
            axis_scores = region.max(axis=0)
            idx = int(np.argmax(axis_scores))
            score = float(axis_scores[idx])
            candidate = x0 + idx
        else:
            y0 = max(0, center_axis - radius)
            y1 = min(height, center_axis + radius + 1)
            x0 = max(0, fixed_axis - radius)
            x1 = min(width, fixed_axis + radius + 1)
            region = strength_map[y0:y1, x0:x1]
            if region.size == 0:
                return None
            axis_scores = region.max(axis=1)
            idx = int(np.argmax(axis_scores))
            score = float(axis_scores[idx])
            candidate = y0 + idx
        if score < float(self._snap_strength_threshold):
            return None
        if abs(int(candidate) - int(center_axis)) > radius:
            return None
        return int(candidate)

    def _paint_main_hover_overlay(self, painter: QPainter) -> None:
        pixmap = self._hover_overlay_pixmap
        if pixmap is None or pixmap.isNull():
            return
        source = self._pixmap_item.pixmap()
        if source.isNull():
            return
        viewport = self.viewport().rect()
        if viewport.width() < 20 or viewport.height() < 20:
            return
        mapped_image = self.mapFromScene(
            QRectF(0.0, 0.0, float(source.width()), float(source.height()))
        ).boundingRect()
        masked_region = mapped_image.intersected(viewport)
        if not masked_region.isEmpty():
            painter.fillRect(masked_region, QColor(18, 18, 18, 238))
        max_width = max(80, int(viewport.width() * 0.62))
        max_height = max(80, int(viewport.height() * 0.74))
        scaled = pixmap.scaled(
            max_width,
            max_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        left = max(6, (viewport.width() - scaled.width()) // 2)
        top = max(int(self._ruler_size_px) + 6, (viewport.height() - scaled.height()) // 2)
        rect = QRect(left, top, scaled.width(), scaled.height())
        painter.save()
        painter.fillRect(rect.adjusted(-6, -6, 6, 6), QColor(0, 0, 0, 150))
        painter.drawPixmap(rect.topLeft(), scaled)
        painter.setPen(QPen(QColor(255, 255, 255, 240), 2))
        painter.drawRect(rect)
        painter.restore()

    def _paint_floating_magnifier(self, painter: QPainter) -> None:
        if not self._magnifier_enabled or not self._hover_inside_image or self._hover_scene_point is None:
            return
        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return
        patch = self._build_zoom_patch(
            pixmap,
            self._hover_scene_point,
            zoom_factor=self._magnifier_zoom,
            output_size=self._magnifier_size_px,
        )
        if patch is None or patch.isNull():
            return
        viewport = self.viewport().rect()
        margin = 6
        left = int(self._hover_view_point.x()) - patch.width() - 16
        top = int(self._hover_view_point.y()) - (patch.height() // 2)
        left = max(margin, min(left, viewport.width() - patch.width() - margin))
        top = max(
            int(self._ruler_size_px) + margin,
            min(top, viewport.height() - patch.height() - margin),
        )
        rect = QRect(left, top, patch.width(), patch.height())
        painter.save()
        painter.fillRect(rect.adjusted(-4, -4, 4, 4), QColor(18, 18, 18, 210))
        painter.drawPixmap(rect.topLeft(), patch)
        painter.setPen(QPen(QColor(255, 255, 255, 245), 2))
        painter.drawRect(rect)
        painter.restore()

    def _build_zoom_patch(
        self,
        source: QPixmap,
        center: QPointF,
        *,
        zoom_factor: int,
        output_size: int,
    ) -> QPixmap | None:
        if source.isNull():
            return None
        zoom = max(2, int(zoom_factor))
        size = max(48, int(output_size))
        sample_span = max(6, round(size / float(zoom)))
        half_span = max(3, sample_span // 2)
        x_pos = round(center.x()) - half_span
        y_pos = round(center.y()) - half_span
        max_x = max(0, source.width() - sample_span)
        max_y = max(0, source.height() - sample_span)
        x_pos = max(0, min(x_pos, max_x))
        y_pos = max(0, min(y_pos, max_y))
        cropped = source.copy(x_pos, y_pos, sample_span, sample_span)
        if cropped.isNull():
            return None
        zoomed = cropped.scaled(
            size,
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        painter = QPainter(zoomed)
        try:
            mid = size // 2
            painter.setPen(QPen(QColor(0, 0, 0, 210), 3))
            painter.drawLine(0, mid, size, mid)
            painter.drawLine(mid, 0, mid, size)
            painter.setPen(QPen(QColor(255, 255, 255, 250), 1))
            painter.drawLine(0, mid, size, mid)
            painter.drawLine(mid, 0, mid, size)
        finally:
            painter.end()
        return zoomed

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
