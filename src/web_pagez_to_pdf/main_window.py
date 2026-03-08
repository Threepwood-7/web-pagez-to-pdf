"""Main application window for capture, editor tweaks, and multi-format export."""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageQt
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from threep_commons.paths import resolve_app_data_dir

from . import widget_naming
from .capture_service import WindowCaptureService
from .constants import APP_DISPLAY_NAME, APP_IDENTITY
from .exporters import run_export, sanitize_basename
from .hotkeys import GlobalHotkeyPoller
from .image_processing import (
    PAPER_SIZES,
    apply_edit_transform,
    compute_page_slices,
    pil_to_qpixmap,
    suggest_navigation_crop,
)
from .models import (
    CaptureItem,
    EditAdjustments,
    ExportFormats,
    ExportRequest,
    PrintLayout,
)
from .scroll_capture import ScrollCaptureOptions, run_full_page_capture
from .stop_overlay import HoverStopOverlay
from .target_picker import CrosshairPickerOverlay, PickedWindow, WindowPickerDialog


class FullCaptureWorker(QThread):
    capture_succeeded = Signal(object)
    capture_failed = Signal(str)

    def __init__(
        self,
        capture_service: WindowCaptureService,
        target_hwnd: int,
        options: ScrollCaptureOptions,
        stop_event: threading.Event,
    ) -> None:
        super().__init__()
        self._capture_service = capture_service
        self._target_hwnd = target_hwnd
        self._options = options
        self._stop_event = stop_event

    def run(self) -> None:
        try:
            result = run_full_page_capture(
                service=self._capture_service,
                target_hwnd=self._target_hwnd,
                options=self._options,
                stop_requested=self._stop_event.is_set,
            )
        except Exception as exc:  # pragma: no cover
            self.capture_failed.emit(str(exc))
            return
        self.capture_succeeded.emit(result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.window_id = "main"
        self._capture_service = WindowCaptureService()
        self._hotkeys = GlobalHotkeyPoller()
        self._stop_overlay = HoverStopOverlay()
        self._selected_target: PickedWindow | None = None
        self._queue: list[CaptureItem] = []
        self._stop_event = threading.Event()
        self._capture_worker: FullCaptureWorker | None = None
        self._crosshair_overlay: CrosshairPickerOverlay | None = None
        self._build_ui()
        self._bind_events()
        self._apply_start_geometry()
        self._load_defaults()
        self._hotkeys.start()

    def _build_ui(self) -> None:
        self._assign_widget_identity(self, widget_naming.window_widget_id(self.window_id), "window")
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setMinimumSize(980, 300)
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        top = QWidget()
        top_grid = QGridLayout(top)
        self.target_label = QLabel("Target: none")
        self.pick_list_button = QPushButton("Pick Window")
        self.pick_crosshair_button = QPushButton("Pick Crosshair")
        self.capture_button = QPushButton("Capture (Ctrl+Shift+C)")
        self.capture_full_button = QPushButton("Capture Full (Ctrl+Shift+S)")
        self.stop_button = QPushButton("Stop (Ctrl+Shift+X)")
        self.import_button = QPushButton("Open Image(s)")
        for widget, control in (
            (self.target_label, "target_label"),
            (self.pick_list_button, "pick_list_button"),
            (self.pick_crosshair_button, "pick_crosshair_button"),
            (self.capture_button, "capture_button"),
            (self.capture_full_button, "capture_full_button"),
            (self.stop_button, "stop_button"),
            (self.import_button, "import_button"),
        ):
            self._assign_control_identity(widget, control, control)
        self.max_pages_spin = QSpinBox()
        self.max_pages_spin.setRange(2, 300)
        self.max_pages_spin.setValue(18)
        top_grid.addWidget(self.target_label, 0, 0, 1, 5)
        top_grid.addWidget(self.pick_list_button, 1, 0)
        top_grid.addWidget(self.pick_crosshair_button, 1, 1)
        top_grid.addWidget(self.capture_button, 1, 2)
        top_grid.addWidget(self.capture_full_button, 1, 3)
        top_grid.addWidget(self.stop_button, 1, 4)
        top_grid.addWidget(self.import_button, 2, 4)
        top_grid.addWidget(QLabel("max_capture_pages"), 2, 2)
        top_grid.addWidget(self.max_pages_spin, 2, 3)
        layout.addWidget(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.queue_list = QListWidget()
        self._assign_control_identity(self.queue_list, "queue_list", "queue_list")
        left_layout.addWidget(QLabel("Queue"))
        left_layout.addWidget(self.queue_list, stretch=1)
        queue_row = QHBoxLayout()
        self.up_button = QPushButton("Up")
        self.down_button = QPushButton("Down")
        self.remove_button = QPushButton("Remove")
        for button in (self.up_button, self.down_button, self.remove_button):
            queue_row.addWidget(button)
        left_layout.addLayout(queue_row)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.preview_scroll = QScrollArea()
        self._assign_control_identity(self.preview_scroll, "preview_scroll", "preview_scroll")
        self.preview_scroll.setWidgetResizable(True)
        self.preview_label = QLabel("No capture selected")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_scroll.setWidget(self.preview_label)
        right_layout.addWidget(self.preview_scroll, stretch=1)

        form = QFormLayout()
        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(10.0, 400.0)
        self.zoom_spin.setValue(100.0)
        self.zoom_spin.setSuffix(" %")
        self.rotate_spin = QSpinBox()
        self.rotate_spin.setRange(0, 359)
        self.crop_left_spin = QSpinBox()
        self.crop_right_spin = QSpinBox()
        self.crop_top_spin = QSpinBox()
        self.crop_bottom_spin = QSpinBox()
        for spin in (self.crop_left_spin, self.crop_right_spin, self.crop_top_spin, self.crop_bottom_spin):
            spin.setRange(0, 20000)
        self.auto_crop_button = QPushButton("Analyze Nav Crop")
        self.split_spin = QSpinBox()
        self.split_spin.setRange(0, 1000000)
        self.add_split_button = QPushButton("Add Split Marker")
        self.split_list = QListWidget()
        self.remove_split_button = QPushButton("Remove Split Marker")
        self.preview_breaks_button = QPushButton("Preview Breaks")
        form.addRow("Zoom", self.zoom_spin)
        form.addRow("Rotate", self.rotate_spin)
        form.addRow("Crop L/R/T/B", self._row_widget([self.crop_left_spin, self.crop_right_spin, self.crop_top_spin, self.crop_bottom_spin]))
        form.addRow(self.auto_crop_button)
        form.addRow("Split Y", self.split_spin)
        form.addRow(self.add_split_button)
        form.addRow(self.split_list)
        form.addRow(self.remove_split_button)
        form.addRow(self.preview_breaks_button)
        right_layout.addLayout(form)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)

        export = QWidget()
        export_grid = QGridLayout(export)
        self.combine_checkbox = QCheckBox("Combine queue")
        self.combine_checkbox.setChecked(True)
        self.pdf_checkbox = QCheckBox("PDF")
        self.pdf_checkbox.setChecked(True)
        self.paged_images_checkbox = QCheckBox("Paged PNG")
        self.long_image_checkbox = QCheckBox("Long PNG")
        self.tiff_checkbox = QCheckBox("TIFF")
        self.docx_checkbox = QCheckBox("DOCX")
        self.pptx_checkbox = QCheckBox("PPTX")
        self.docx_mode_combo = QComboBox()
        self.docx_mode_combo.addItem("Per split-page", "per_split_page")
        self.docx_mode_combo.addItem("Per capture", "per_capture")
        self.paper_combo = QComboBox()
        for paper_name in sorted(PAPER_SIZES.keys()):
            self.paper_combo.addItem(paper_name)
        self.paper_combo.setCurrentText("A4")
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["portrait", "landscape"])
        self.margin_top_spin = QDoubleSpinBox()
        self.margin_bottom_spin = QDoubleSpinBox()
        self.margin_left_spin = QDoubleSpinBox()
        self.margin_right_spin = QDoubleSpinBox()
        self.gutter_spin = QDoubleSpinBox()
        for spin, value in (
            (self.margin_top_spin, 20.0),
            (self.margin_bottom_spin, 20.0),
            (self.margin_left_spin, 15.0),
            (self.margin_right_spin, 15.0),
            (self.gutter_spin, 0.0),
        ):
            spin.setRange(0.0, 60.0)
            spin.setValue(value)
        self.blank_spin = QSpinBox()
        self.blank_spin.setRange(0, 255)
        self.blank_spin.setValue(245)
        self.search_spin = QSpinBox()
        self.search_spin.setRange(20, 2000)
        self.search_spin.setValue(300)
        self.base_input = QLineEdit("capture")
        self.output_input = QLineEdit()
        self.output_browse_button = QPushButton("Browse")
        self.header_input = QTextEdit()
        self.footer_input = QTextEdit()
        self.export_button = QPushButton("Export")
        self.status_label = QLabel("Ready.")
        self._assign_control_identity(self.export_button, "export_button", "export_button")
        self._assign_control_identity(self.status_label, "status_label", "status_label")

        export_grid.addWidget(self.combine_checkbox, 0, 0)
        export_grid.addWidget(self.pdf_checkbox, 0, 1)
        export_grid.addWidget(self.paged_images_checkbox, 0, 2)
        export_grid.addWidget(self.long_image_checkbox, 1, 0)
        export_grid.addWidget(self.tiff_checkbox, 1, 1)
        export_grid.addWidget(self.docx_checkbox, 1, 2)
        export_grid.addWidget(self.pptx_checkbox, 1, 3)
        export_grid.addWidget(QLabel("DOCX/PPTX mode"), 2, 0)
        export_grid.addWidget(self.docx_mode_combo, 2, 1)
        export_grid.addWidget(QLabel("Paper/Orientation"), 2, 2)
        export_grid.addWidget(self._row_widget([self.paper_combo, self.orientation_combo]), 2, 3)
        export_grid.addWidget(QLabel("Margins + Gutter"), 3, 0)
        export_grid.addWidget(self._row_widget([self.margin_top_spin, self.margin_bottom_spin, self.margin_left_spin, self.margin_right_spin, self.gutter_spin]), 3, 1, 1, 3)
        export_grid.addWidget(QLabel("Blank/Search"), 4, 0)
        export_grid.addWidget(self._row_widget([self.blank_spin, self.search_spin]), 4, 1)
        export_grid.addWidget(QLabel("Base Name"), 4, 2)
        export_grid.addWidget(self.base_input, 4, 3)
        export_grid.addWidget(QLabel("Output Folder"), 5, 0)
        export_grid.addWidget(self._row_widget([self.output_input, self.output_browse_button]), 5, 1, 1, 3)
        export_grid.addWidget(QLabel("Header"), 6, 0)
        export_grid.addWidget(self.header_input, 6, 1, 1, 3)
        export_grid.addWidget(QLabel("Footer"), 7, 0)
        export_grid.addWidget(self.footer_input, 7, 1, 1, 3)
        export_grid.addWidget(self.export_button, 8, 3)
        export_grid.addWidget(self.status_label, 9, 0, 1, 4)
        layout.addWidget(export)

    def _bind_events(self) -> None:
        self.pick_list_button.clicked.connect(self._pick_window_from_list)
        self.pick_crosshair_button.clicked.connect(self._pick_window_crosshair)
        self.capture_button.clicked.connect(self._capture_selected_viewport)
        self.capture_full_button.clicked.connect(self._capture_full_scroll)
        self.stop_button.clicked.connect(self._request_stop)
        self.import_button.clicked.connect(self._import_images)
        self.queue_list.currentRowChanged.connect(self._refresh_preview)
        self.up_button.clicked.connect(self._queue_move_up)
        self.down_button.clicked.connect(self._queue_move_down)
        self.remove_button.clicked.connect(self._queue_remove)
        self.auto_crop_button.clicked.connect(self._apply_auto_crop)
        self.add_split_button.clicked.connect(self._add_split_marker)
        self.remove_split_button.clicked.connect(self._remove_split_marker)
        self.preview_breaks_button.clicked.connect(self._preview_breaks)
        self.output_browse_button.clicked.connect(self._browse_output)
        self.export_button.clicked.connect(self._run_export)
        for widget in (
            self.zoom_spin,
            self.rotate_spin,
            self.crop_left_spin,
            self.crop_right_spin,
            self.crop_top_spin,
            self.crop_bottom_spin,
        ):
            widget.valueChanged.connect(self._refresh_preview)
        self._hotkeys.capture_selected_requested.connect(self._capture_selected_viewport)
        self._hotkeys.capture_full_requested.connect(self._capture_full_scroll)
        self._hotkeys.stop_capture_requested.connect(self._request_stop)
        self._stop_overlay.stop_requested.connect(self._request_stop)

    @staticmethod
    def _row_widget(widgets: list[QWidget]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            layout.addWidget(widget)
        return row

    def _apply_start_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width = int(area.width() * 0.60)
        height = 300
        x_pos = area.left() + (area.width() - width) // 2
        y_pos = area.top() + int(area.height() * 0.20)
        self.setGeometry(x_pos, y_pos, width, height)

    def _load_defaults(self) -> None:
        output_dir = resolve_app_data_dir(APP_IDENTITY) / "captures"
        output_dir.mkdir(parents=True, exist_ok=True)
        self.output_input.setText(str(output_dir))

    def _pick_window_from_list(self) -> None:
        windows = self._capture_service.list_top_windows(int(self.winId()))
        if not windows:
            self.status_label.setText("No visible windows to pick.")
            return
        dialog = WindowPickerDialog(windows, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        picked = dialog.selected_window()
        if picked is None:
            return
        self._set_target(picked)

    def _pick_window_crosshair(self) -> None:
        self._crosshair_overlay = CrosshairPickerOverlay(self)
        self._crosshair_overlay.pick_requested.connect(self._pick_from_crosshair_point)
        self._crosshair_overlay.show_fullscreen_on_cursor_screen()
        self.status_label.setText("Crosshair mode active. Click target window or Esc to cancel.")

    def _pick_from_crosshair_point(self, x_pos: int, y_pos: int) -> None:
        hwnd = self._capture_service.window_from_point(x_pos, y_pos)
        if hwnd is None:
            self.status_label.setText("No window found under crosshair.")
            return
        self._set_target(PickedWindow(hwnd=hwnd, label=self._capture_service.window_title(hwnd)))

    def _set_target(self, target: PickedWindow) -> None:
        self._selected_target = target
        self.target_label.setText(f"Target: {target.label} [hwnd={target.hwnd}]")
        if self.base_input.text().strip() in {"", "capture"}:
            self.base_input.setText(sanitize_basename(target.label))
        self.status_label.setText("Target selected.")

    def _capture_selected_viewport(self) -> None:
        if self._selected_target is None:
            self.status_label.setText("Select a target window first.")
            return
        self._capture_service.activate_window(self._selected_target.hwnd)
        pixmap = self._capture_service.capture_window(self._selected_target.hwnd)
        if pixmap is None:
            self.status_label.setText("Capture failed.")
            return
        image = ImageQt.fromqpixmap(pixmap).convert("RGB")
        self._add_capture(image=image, title=self._selected_target.label, source_hwnd=self._selected_target.hwnd, frame_count=1)
        self.status_label.setText("Captured selected viewport.")

    def _capture_full_scroll(self) -> None:
        if self._selected_target is None:
            self.status_label.setText("Select a target window first.")
            return
        if self._capture_worker is not None and self._capture_worker.isRunning():
            self.status_label.setText("Capture already running.")
            return
        self._stop_event.clear()
        self._capture_worker = FullCaptureWorker(
            capture_service=self._capture_service,
            target_hwnd=self._selected_target.hwnd,
            options=ScrollCaptureOptions(max_capture_pages=int(self.max_pages_spin.value())),
            stop_event=self._stop_event,
        )
        self._capture_worker.capture_succeeded.connect(self._full_capture_done)
        self._capture_worker.capture_failed.connect(self._full_capture_failed)
        self._capture_worker.finished.connect(self._full_capture_finished)
        self._stop_overlay.show_top_right()
        self._capture_worker.start()
        self.status_label.setText("Full capture running. Hover red stop badge or press Ctrl+Shift+X.")

    def _request_stop(self) -> None:
        self._stop_event.set()
        self.status_label.setText("Stop requested...")

    def _full_capture_done(self, result_obj: object) -> None:
        image = getattr(result_obj, "image", None)
        if image is None:
            self.status_label.setText("Invalid full capture result.")
            return
        self._add_capture(
            image=image,
            title=self._selected_target.label if self._selected_target else "capture",
            source_hwnd=self._selected_target.hwnd if self._selected_target else None,
            frame_count=int(getattr(result_obj, "captured_frames", 0)),
        )
        ended_by_repeat = bool(getattr(result_obj, "ended_by_repeat", False))
        suffix = " (auto-stopped by repeated frame detection)." if ended_by_repeat else "."
        self.status_label.setText(f"Full capture complete{suffix}")

    def _full_capture_failed(self, message: str) -> None:
        self.status_label.setText(f"Full capture failed: {message}")

    def _full_capture_finished(self) -> None:
        self._stop_overlay.hide()
        self._capture_worker = None

    def _add_capture(
        self,
        image: Image.Image,
        title: str,
        source_hwnd: int | None,
        frame_count: int | None,
    ) -> None:
        output_dir = Path(self.output_input.text().strip())
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = sanitize_basename(f"{title}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}")
        path = output_dir / f"{stem}.png"
        image.save(path, format="PNG")
        item = CaptureItem(
            item_id=uuid.uuid4().hex,
            title=title,
            image_path=path,
            source_hwnd=source_hwnd,
            frame_count=frame_count,
        )
        self._queue.append(item)
        list_item = QListWidgetItem(f"{item.title} [{item.image_path.name}]")
        self.queue_list.addItem(list_item)
        self.queue_list.setCurrentRow(self.queue_list.count() - 1)

    def _import_images(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "Open Images",
            str(Path.home()),
            "Image files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
        )
        added = 0
        for path_text in paths:
            path = Path(path_text)
            if not path.exists():
                continue
            try:
                image = Image.open(path).convert("RGB")
            except Exception:
                continue
            self._add_capture(image=image, title=path.stem, source_hwnd=None, frame_count=None)
            added += 1
        self.status_label.setText(f"Imported {added} image(s).")

    def _queue_move_up(self) -> None:
        row = self.queue_list.currentRow()
        if row <= 0:
            return
        self._queue[row - 1], self._queue[row] = self._queue[row], self._queue[row - 1]
        item = self.queue_list.takeItem(row)
        self.queue_list.insertItem(row - 1, item)
        self.queue_list.setCurrentRow(row - 1)

    def _queue_move_down(self) -> None:
        row = self.queue_list.currentRow()
        if row < 0 or row >= self.queue_list.count() - 1:
            return
        self._queue[row + 1], self._queue[row] = self._queue[row], self._queue[row + 1]
        item = self.queue_list.takeItem(row)
        self.queue_list.insertItem(row + 1, item)
        self.queue_list.setCurrentRow(row + 1)

    def _queue_remove(self) -> None:
        row = self.queue_list.currentRow()
        if row < 0:
            return
        self.queue_list.takeItem(row)
        self._queue.pop(row)
        self._refresh_preview()

    def _current_item(self) -> CaptureItem | None:
        row = self.queue_list.currentRow()
        if row < 0 or row >= len(self._queue):
            return None
        return self._queue[row]

    def _collect_edits(self) -> EditAdjustments:
        return EditAdjustments(
            crop_left_px=int(self.crop_left_spin.value()),
            crop_right_px=int(self.crop_right_spin.value()),
            crop_top_px=int(self.crop_top_spin.value()),
            crop_bottom_px=int(self.crop_bottom_spin.value()),
            split_markers_px=self._split_markers(),
        )

    def _collect_layout(self) -> PrintLayout:
        return PrintLayout(
            paper_name=self.paper_combo.currentText(),
            orientation=self.orientation_combo.currentText(),
            margin_top_mm=float(self.margin_top_spin.value()),
            margin_bottom_mm=float(self.margin_bottom_spin.value()),
            margin_left_mm=float(self.margin_left_spin.value()),
            margin_right_mm=float(self.margin_right_spin.value()),
            gutter_mm=float(self.gutter_spin.value()),
            blank_row_threshold=int(self.blank_spin.value()),
            search_window_px=int(self.search_spin.value()),
            zoom_percent=float(self.zoom_spin.value()),
            rotate_degrees=int(self.rotate_spin.value()),
            header_rich_text=self.header_input.toHtml(),
            footer_rich_text=self.footer_input.toHtml(),
        )

    def _refresh_preview(self, *_args: object) -> None:
        item = self._current_item()
        if item is None:
            self.preview_label.setPixmap(None)
            self.preview_label.setText("No capture selected")
            return
        if not item.image_path.exists():
            self.preview_label.setPixmap(None)
            self.preview_label.setText("Capture file missing")
            return
        image = Image.open(item.image_path).convert("RGB")
        preview = apply_edit_transform(image, self._collect_layout(), self._collect_edits())
        self.preview_label.setPixmap(pil_to_qpixmap(preview))
        self.preview_label.setText("")

    def _apply_auto_crop(self) -> None:
        item = self._current_item()
        if item is None:
            self.status_label.setText("Select queue item first.")
            return
        image = Image.open(item.image_path).convert("RGB")
        left, right = suggest_navigation_crop(image)
        self.crop_left_spin.setValue(left)
        self.crop_right_spin.setValue(right)
        self._refresh_preview()
        self.status_label.setText(f"Suggested crop left={left}px right={right}px")

    def _add_split_marker(self) -> None:
        value = int(self.split_spin.value())
        if value <= 0:
            return
        self.split_list.addItem(QListWidgetItem(str(value)))

    def _remove_split_marker(self) -> None:
        row = self.split_list.currentRow()
        if row < 0:
            return
        self.split_list.takeItem(row)

    def _split_markers(self) -> list[int]:
        markers: list[int] = []
        for index in range(self.split_list.count()):
            item = self.split_list.item(index)
            try:
                markers.append(int(item.text()))
            except ValueError:
                continue
        return sorted(set(markers))

    def _preview_breaks(self) -> None:
        item = self._current_item()
        if item is None:
            self.status_label.setText("Select queue item first.")
            return
        image = Image.open(item.image_path).convert("RGB")
        transformed = apply_edit_transform(image, self._collect_layout(), self._collect_edits())
        slices = compute_page_slices(transformed, self._collect_layout(), self._split_markers())
        self.status_label.setText(f"Predicted page slices: {len(slices)}")

    def _browse_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder",
            self.output_input.text().strip() or str(Path.home()),
        )
        if selected:
            self.output_input.setText(selected)

    def _run_export(self) -> None:
        if not self._queue:
            self.status_label.setText("Queue is empty.")
            return
        if self.combine_checkbox.isChecked():
            captures = self._queue[:]
        else:
            item = self._current_item()
            if item is None:
                self.status_label.setText("Select queue item first.")
                return
            captures = [item]
        request = ExportRequest(
            captures=captures,
            combine_mode=self.combine_checkbox.isChecked(),
            formats=ExportFormats(
                pdf=self.pdf_checkbox.isChecked(),
                paged_images=self.paged_images_checkbox.isChecked(),
                long_image=self.long_image_checkbox.isChecked(),
                tiff=self.tiff_checkbox.isChecked(),
                docx=self.docx_checkbox.isChecked(),
                pptx=self.pptx_checkbox.isChecked(),
            ),
            output_dir=Path(self.output_input.text().strip()),
            basename=sanitize_basename(f"{self.base_input.text().strip()}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"),
            docx_pptx_mode=str(self.docx_mode_combo.currentData()),
            layout=self._collect_layout(),
            edits=self._collect_edits(),
        )
        if not any(vars(request.formats).values()):
            self.status_label.setText("Pick at least one output format.")
            return
        try:
            result = run_export(request)
        except Exception as exc:
            self.status_label.setText(f"Export failed: {exc}")
            return
        self.status_label.setText(f"Exported {len(result.generated_paths)} file(s).")
        if result.generated_paths:
            with suppress(OSError):
                os.startfile(str(result.generated_paths[0]))

    def _assign_control_identity(self, widget: QWidget, control: str, alias: str) -> None:
        widget_id = widget_naming.control_widget_id(self.window_id, control)
        self._assign_widget_identity(widget, widget_id, alias)

    @staticmethod
    def _assign_widget_identity(widget: QWidget, widget_id: str, alias: str) -> None:
        widget.setObjectName(widget_naming.object_name_for_id(widget_id))
        widget.setProperty("widget_id", widget_id)
        widget.setProperty("widget_alias", alias)

    def closeEvent(self, event) -> None:
        self._request_stop()
        self._hotkeys.stop()
        self._stop_overlay.hide()
        super().closeEvent(event)
