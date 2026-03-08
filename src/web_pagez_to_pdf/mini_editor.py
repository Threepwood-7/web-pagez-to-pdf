"""Non-modal advanced editor window for per-item capture adjustments."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFormLayout,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .image_processing import (
    apply_edit_transform,
    pil_to_qpixmap,
    suggest_navigation_crop,
)
from .models import CaptureItem, EditAdjustments, PrintLayout

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent, QWheelEvent


class _EditorCanvas(QGraphicsView):
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
        self._zoom_steps = 0

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(self.renderHints())
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

    def set_image(self, pixmap: QPixmap) -> None:
        """Load pixmap and reset camera fit."""

        self._pixmap_item.setPixmap(pixmap)
        rect = QRectF(pixmap.rect())
        self._scene.setSceneRect(rect)
        self.resetTransform()
        self._zoom_steps = 0
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._clear_overlay()

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
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.scale(factor, factor)
        self._zoom_steps += 1 if delta > 0 else -1

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._tool == "pan":
            super().mousePressEvent(event)
            return

        scene_point = self.mapToScene(event.position().toPoint())
        if self._tool in {"crop_rect", "redact"}:
            self._drag_origin = scene_point
            self._clear_overlay()
            self._rect_item = QGraphicsRectItem(QRectF(scene_point, scene_point))
            pen_color = Qt.GlobalColor.green if self._tool == "crop_rect" else Qt.GlobalColor.red
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


class MiniEditorWindow(QMainWindow):
    """Single-instance non-modal editor for advanced non-destructive operations."""

    session_changed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item: CaptureItem | None = None
        self._base_image: Image.Image | None = None
        self._session = EditAdjustments()
        self._loading = False
        self._build_ui()
        self._bind_events()

    def _build_ui(self) -> None:
        self.setWindowTitle("Mini Editor")
        self.resize(1280, 760)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setWindowModality(Qt.WindowModality.NonModal)

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.canvas = _EditorCanvas(self)
        layout.addWidget(self.canvas, 3)

        side = QWidget(self)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)
        layout.addWidget(side, 1)

        self.item_label = QLabel("No queue item selected.", side)
        side_layout.addWidget(self.item_label)

        tool_group = QGroupBox("Tools", side)
        tools_layout = QHBoxLayout(tool_group)
        self.tool_buttons = QButtonGroup(self)
        self.tool_buttons.setExclusive(True)
        self.pan_tool = self._new_tool_button("Pan", "pan", checked=True)
        self.crop_rect_tool = self._new_tool_button("Rect Crop", "crop_rect")
        self.crop_free_tool = self._new_tool_button("Free Crop", "crop_free")
        self.redact_tool = self._new_tool_button("Redact", "redact")
        for button in (
            self.pan_tool,
            self.crop_rect_tool,
            self.crop_free_tool,
            self.redact_tool,
        ):
            tools_layout.addWidget(button)
        side_layout.addWidget(tool_group)

        transform_group = QGroupBox("Transform", side)
        transform_form = QFormLayout(transform_group)
        self.rotate_spin = QSpinBox(transform_group)
        self.rotate_spin.setRange(-180, 180)
        self.scale_spin = QSpinBox(transform_group)
        self.scale_spin.setRange(10, 400)
        self.scale_spin.setValue(100)
        self.scale_spin.setSuffix(" %")
        self.straighten_spin = QSpinBox(transform_group)
        self.straighten_spin.setRange(-15, 15)
        transform_form.addRow("Rotate", self.rotate_spin)
        transform_form.addRow("Scale", self.scale_spin)
        transform_form.addRow("Straighten", self.straighten_spin)
        side_layout.addWidget(transform_group)

        split_group = QGroupBox("Split Markers", side)
        split_layout = QVBoxLayout(split_group)
        split_row = QHBoxLayout()
        self.split_spin = QSpinBox(split_group)
        self.split_spin.setRange(0, 100000)
        self.split_add_button = QPushButton("Add", split_group)
        self.split_remove_button = QPushButton("Remove", split_group)
        split_row.addWidget(self.split_spin)
        split_row.addWidget(self.split_add_button)
        split_row.addWidget(self.split_remove_button)
        split_layout.addLayout(split_row)
        self.split_list = QListWidget(split_group)
        split_layout.addWidget(self.split_list)
        side_layout.addWidget(split_group)

        ops_group = QGroupBox("Operations", side)
        ops_layout = QVBoxLayout(ops_group)
        self.auto_crop_button = QPushButton("Suggest Nav Auto-Crop", ops_group)
        self.clear_redactions_button = QPushButton("Clear Redactions", ops_group)
        self.reset_button = QPushButton("Reset Item Edits", ops_group)
        ops_layout.addWidget(self.auto_crop_button)
        ops_layout.addWidget(self.clear_redactions_button)
        ops_layout.addWidget(self.reset_button)
        side_layout.addWidget(ops_group)
        side_layout.addStretch(1)

    def _new_tool_button(self, text: str, tool: str, checked: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setProperty("tool", tool)
        self.tool_buttons.addButton(button)
        return button

    def _bind_events(self) -> None:
        self.tool_buttons.buttonClicked.connect(self._on_tool_changed)
        self.rotate_spin.valueChanged.connect(self._on_transform_changed)
        self.scale_spin.valueChanged.connect(self._on_transform_changed)
        self.straighten_spin.valueChanged.connect(self._on_transform_changed)
        self.auto_crop_button.clicked.connect(self._suggest_nav_crop)
        self.clear_redactions_button.clicked.connect(self._clear_redactions)
        self.reset_button.clicked.connect(self._reset_session)
        self.split_add_button.clicked.connect(self._add_split_marker)
        self.split_remove_button.clicked.connect(self._remove_split_marker)
        self.canvas.rect_drawn.connect(self._on_canvas_rect_drawn)
        self.canvas.free_crop_drawn.connect(self._on_canvas_free_crop)

    def bind_item(self, item: CaptureItem, session: EditAdjustments) -> None:
        """Bind editor to selected queue item and preloaded session."""

        self._item = item
        self._session = session.clone()
        self.item_label.setText(f"Editing: {item.title} [{item.image_path.name}]")
        try:
            self._base_image = Image.open(item.image_path).convert("RGB")
        except Exception:
            self._base_image = None
            self.item_label.setText(f"Editing: {item.title} [image missing]")
        self._sync_controls_from_session()
        self._refresh_preview()

    def update_session(self, item_id: str, session: EditAdjustments) -> None:
        """Refresh controls for current item when main window updates session state."""

        if self._item is None or self._item.item_id != item_id:
            return
        self._session = session.clone()
        self._sync_controls_from_session()
        self._refresh_preview()

    def _on_tool_changed(self, button: QToolButton) -> None:
        tool = str(button.property("tool") or "pan")
        self.canvas.set_tool(tool)

    def _on_transform_changed(self, *_args: object) -> None:
        if self._loading:
            return
        self._set_scalar_operation("rotate", "degrees", int(self.rotate_spin.value()), neutral=0)
        self._set_scalar_operation("scale", "percent", int(self.scale_spin.value()), neutral=100)
        self._set_scalar_operation(
            "straighten", "degrees", int(self.straighten_spin.value()), neutral=0
        )
        self._emit_session_changed()
        self._refresh_preview()

    def _set_scalar_operation(
        self, op_type: str, key: str, value: int, *, neutral: int
    ) -> None:
        if value == neutral:
            self._session.remove_operation(op_type)
            return
        self._session.set_operation(op_type, {key: value})

    def _suggest_nav_crop(self) -> None:
        if self._base_image is None:
            return
        left, right = suggest_navigation_crop(self._base_image)
        if left <= 0 and right <= 0:
            self._session.remove_operation("nav_auto_crop")
        else:
            self._session.set_operation("nav_auto_crop", {"left": left, "right": right})
        self._emit_session_changed()
        self._refresh_preview()

    def _clear_redactions(self) -> None:
        self._session.remove_operation("redact_rects")
        self._emit_session_changed()
        self._refresh_preview()

    def _reset_session(self) -> None:
        self._session = EditAdjustments()
        self._sync_controls_from_session()
        self._emit_session_changed()
        self._refresh_preview()

    def _add_split_marker(self) -> None:
        marker = int(self.split_spin.value())
        if marker <= 0:
            return
        markers = sorted({*self._session.split_markers_px, marker})
        self._session.split_markers_px = markers
        self._sync_split_list()
        self._emit_session_changed()

    def _remove_split_marker(self) -> None:
        row = self.split_list.currentRow()
        if row < 0 or row >= len(self._session.split_markers_px):
            return
        markers = list(self._session.split_markers_px)
        markers.pop(row)
        self._session.split_markers_px = markers
        self._sync_split_list()
        self._emit_session_changed()

    def _on_canvas_rect_drawn(self, tool: str, rect_obj: object) -> None:
        rect = rect_obj if isinstance(rect_obj, QRectF) else QRectF()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        payload = {
            "left": int(max(0.0, rect.left())),
            "top": int(max(0.0, rect.top())),
            "width": int(max(1.0, rect.width())),
            "height": int(max(1.0, rect.height())),
        }
        if tool == "crop_rect":
            self._session.set_operation("crop_rect", payload)
        elif tool == "redact":
            redaction = {
                "x": payload["left"],
                "y": payload["top"],
                "width": payload["width"],
                "height": payload["height"],
            }
            op = self._session.get_operation("redact_rects")
            rectangles: list[dict[str, int]] = []
            if op is not None:
                raw = op.params.get("rectangles")
                if isinstance(raw, list):
                    for row in raw:
                        if isinstance(row, dict):
                            rectangles.append(deepcopy(row))
            rectangles.append(redaction)
            self._session.set_operation("redact_rects", {"rectangles": rectangles})
        self._emit_session_changed()
        self._refresh_preview()

    def _on_canvas_free_crop(self, points_obj: object) -> None:
        points = points_obj if isinstance(points_obj, list) else []
        normalized: list[list[int]] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            normalized.append([int(point[0]), int(point[1])])
        if len(normalized) < 3:
            return
        self._session.set_operation("crop_free", {"points": normalized})
        self._emit_session_changed()
        self._refresh_preview()

    def _sync_controls_from_session(self) -> None:
        self._loading = True
        try:
            rotate = 0
            rotate_op = self._session.get_operation("rotate")
            if rotate_op is not None:
                rotate = int(rotate_op.params.get("degrees", 0))
            scale = 100
            scale_op = self._session.get_operation("scale")
            if scale_op is not None:
                scale = int(scale_op.params.get("percent", 100))
            straighten = 0
            straighten_op = self._session.get_operation("straighten")
            if straighten_op is not None:
                straighten = int(straighten_op.params.get("degrees", 0))
            self.rotate_spin.setValue(rotate)
            self.scale_spin.setValue(scale)
            self.straighten_spin.setValue(straighten)
            self._sync_split_list()
        finally:
            self._loading = False

    def _sync_split_list(self) -> None:
        self.split_list.clear()
        for marker in sorted({int(v) for v in self._session.split_markers_px if int(v) > 0}):
            self.split_list.addItem(QListWidgetItem(str(marker)))

    def _refresh_preview(self) -> None:
        if self._base_image is None:
            self.canvas.set_image(QPixmap())
            return
        layout = PrintLayout(zoom_percent=100.0, rotate_degrees=0)
        preview = apply_edit_transform(self._base_image, layout, self._session)
        self.canvas.set_image(pil_to_qpixmap(preview))

    def _emit_session_changed(self) -> None:
        if self._item is None:
            return
        self.session_changed.emit(self._item.item_id, self._session.clone())
