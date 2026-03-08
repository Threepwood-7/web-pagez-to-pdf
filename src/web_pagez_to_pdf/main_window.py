"""Main application window for capture, editor tweaks, and multi-format export."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageQt
from PySide6.QtCore import QEvent, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
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
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from threep_commons.paths import resolve_app_data_dir

from . import widget_naming
from .capture_service import (
    CAPTURE_BACKENDS,
    CAPTURE_LOGGER_NAME,
    DEFAULT_CAPTURE_BACKEND,
    WindowCaptureService,
)
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
from .mini_editor import MiniEditorWindow
from .models import (
    CaptureItem,
    EditAdjustments,
    EditorSessionState,
    ExportFormats,
    ExportRequest,
    PrintLayout,
)
from .scroll_capture import (
    CAPTURE_LOG_LEVELS,
    CENTER_CLICK_ASSIST_MODES,
    CURSOR_HOLD_MODES,
    DEFAULT_CAPTURE_LOG_LEVEL,
    DEFAULT_CENTER_CLICK_ASSIST,
    DEFAULT_CURSOR_HOLD_MODE,
    DEFAULT_SCROLL_STRATEGY,
    DEFAULT_WHEEL_INJECTION_MODE,
    SCROLL_STRATEGIES,
    WHEEL_INJECTION_MODES,
    ScrollCaptureOptions,
    ScrollCaptureProgress,
    normalize_capture_log_level,
    run_full_page_capture,
)
from .settings_window import SettingsWindow
from .stop_overlay import HoverStopOverlay
from .target_picker import CrosshairPickerOverlay, PickedWindow, WindowPickerDialog

BROWSER_PROCESS_PRIORITY = (
    "msedge.exe",
    "chrome.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "arc.exe",
)


class FullCaptureWorker(QThread):
    """Worker thread wrapper for full-scroll capture flow."""

    capture_succeeded = Signal(object)
    capture_failed = Signal(str)
    capture_progress = Signal(object)

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
        def _emit_progress(progress_obj: ScrollCaptureProgress) -> None:
            self.capture_progress.emit(progress_obj)

        try:
            result = run_full_page_capture(
                service=self._capture_service,
                target_hwnd=self._target_hwnd,
                options=self._options,
                stop_requested=self._stop_event.is_set,
                progress_callback=_emit_progress,
            )
        except Exception as exc:  # pragma: no cover
            self.capture_failed.emit(str(exc))
            return
        self.capture_succeeded.emit(result)


class MainWindow(QMainWindow):
    """Tabbed capture/editor/export window with mini-editor integration."""

    def __init__(self) -> None:
        super().__init__()
        self.window_id = "main"
        self._settings = QSettings()
        self._capture_service = WindowCaptureService()
        self._hotkeys = GlobalHotkeyPoller()
        self._stop_overlay = HoverStopOverlay()
        self._selected_target: PickedWindow | None = None
        self._queue: list[CaptureItem] = []
        self._sessions = EditorSessionState()
        self._stop_event = threading.Event()
        self._capture_worker: FullCaptureWorker | None = None
        self._crosshair_overlay: CrosshairPickerOverlay | None = None
        self._mini_editor: MiniEditorWindow | None = None
        self._settings_window: SettingsWindow | None = None
        self._editor_sync_guard = False
        self._format_sync_guard = False
        self._editor_zoom_mode = "fit_height"
        self._editor_manual_zoom_percent = 100
        self._build_ui()
        self._bind_events()
        self._apply_start_geometry()
        self._load_defaults()
        self._load_runtime_settings()
        self._select_default_browser_target()
        self._hotkeys.start()

    def _build_ui(self) -> None:
        self._assign_widget_identity(self, widget_naming.window_widget_id(self.window_id), "window")
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setMinimumSize(1120, 680)
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._build_menu()
        top = QWidget(self)
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        self.target_label = QLabel("Target: none")
        self.pick_list_button = QPushButton("Pick")
        self.pick_crosshair_button = QPushButton("Crosshair")
        self.capture_button = QPushButton("Capture (Ctrl+Shift+C)")
        self.capture_full_button = QPushButton("Capture Full (Ctrl+Shift+S)")
        self.capture_last_selected_button = QPushButton("Capture Last Selected Window")
        self.stop_button = QPushButton("Stop (Ctrl+Shift+X)")
        self.import_button = QPushButton("Import")
        for widget, control in (
            (self.target_label, "target_label"),
            (self.pick_list_button, "pick_list_button"),
            (self.pick_crosshair_button, "pick_crosshair_button"),
            (self.capture_button, "capture_button"),
            (self.capture_full_button, "capture_full_button"),
            (self.capture_last_selected_button, "capture_last_selected_button"),
            (self.stop_button, "stop_button"),
            (self.import_button, "import_button"),
        ):
            self._assign_control_identity(widget, control, control)
        top_row.addWidget(self.target_label, 2)
        top_row.addWidget(self.pick_list_button)
        top_row.addWidget(self.pick_crosshair_button)
        top_row.addWidget(self.capture_button)
        top_row.addWidget(self.capture_full_button)
        top_row.addWidget(self.capture_last_selected_button)
        top_row.addWidget(self.stop_button)
        top_row.addWidget(self.import_button)
        self.quick_pdf_checkbox = QCheckBox("PDF")
        self.quick_paged_checkbox = QCheckBox("Paged")
        self.quick_long_checkbox = QCheckBox("Long")
        self.quick_tiff_checkbox = QCheckBox("TIFF")
        self.quick_docx_checkbox = QCheckBox("DOCX")
        self.quick_pptx_checkbox = QCheckBox("PPTX")
        self.quick_export_button = QPushButton("Export")
        for widget in (
            self.quick_pdf_checkbox,
            self.quick_paged_checkbox,
            self.quick_long_checkbox,
            self.quick_tiff_checkbox,
            self.quick_docx_checkbox,
            self.quick_pptx_checkbox,
            self.quick_export_button,
        ):
            top_row.addWidget(widget)
        layout.addWidget(top)

        self.tabs = QTabWidget(self)
        self._assign_control_identity(self.tabs, "workflow_tabs", "workflow_tabs")
        layout.addWidget(self.tabs, stretch=1)

        capture_tab = QWidget(self)
        capture_layout = QVBoxLayout(capture_tab)
        split = QSplitter(Qt.Orientation.Horizontal, capture_tab)
        capture_layout.addWidget(split, stretch=1)
        left = QWidget(split)
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
        self.queue_summary_label = QLabel("Queue: 0 item(s)")
        left_layout.addWidget(self.queue_summary_label)
        right = QWidget(split)
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Latest / Selected Thumbnail"))
        self.capture_tab_preview_label = QLabel("No capture selected", right)
        self.capture_tab_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.capture_tab_preview_label.setMinimumHeight(220)
        self.capture_tab_preview_label.setStyleSheet(
            "border: 1px solid rgba(130,130,130,0.6); background: rgba(20,20,20,0.05);"
        )
        self._assign_control_identity(
            self.capture_tab_preview_label,
            "capture_tab_preview_label",
            "capture_tab_preview_label",
        )
        right_layout.addWidget(self.capture_tab_preview_label)

        self.capture_advanced_group = QGroupBox("Advanced Capture", right)
        cap_adv_layout = QVBoxLayout(self.capture_advanced_group)
        cap_grid = QGridLayout()
        self.max_pages_spin = QSpinBox()
        self.max_pages_spin.setRange(2, 300)
        self.max_pages_spin.setValue(18)
        self.capture_delay_spin = QSpinBox()
        self.capture_delay_spin.setRange(120, 2000)
        self.capture_delay_spin.setSuffix(" ms")
        self.auto_target_checkbox = QCheckBox("Auto-pick second last active window")
        self.pick_second_last_button = QPushButton("Use Second Last Target")
        self.capture_backend_combo = QComboBox()
        self.capture_backend_combo.addItem("Screen Region (GDI)", "screen_region_gdi")
        self.capture_backend_combo.addItem("Qt grabWindow", "qt_grab_window")
        self.capture_backend_combo.addItem("PrintWindow", "print_window")
        self._assign_control_identity(
            self.capture_backend_combo,
            "capture_backend_combo",
            "capture_backend_combo",
        )
        self.capture_scroll_strategy_combo = QComboBox()
        self.capture_scroll_strategy_combo.addItem("Hybrid Wheel + PageDown", "hybrid_wheel_pagedown")
        self.capture_scroll_strategy_combo.addItem("PageDown only", "pagedown_only")
        self.capture_scroll_strategy_combo.addItem("Wheel only", "wheel_only")
        self._assign_control_identity(
            self.capture_scroll_strategy_combo,
            "capture_scroll_strategy_combo",
            "capture_scroll_strategy_combo",
        )
        self.capture_wheel_injection_combo = QComboBox()
        self.capture_wheel_injection_combo.addItem(
            "Physical Center (SendInput)",
            "physical_center_sendinput",
        )
        self.capture_wheel_injection_combo.addItem(
            "Legacy WM_MOUSEWHEEL",
            "legacy_message_wheel",
        )
        self._assign_control_identity(
            self.capture_wheel_injection_combo,
            "capture_wheel_injection_combo",
            "capture_wheel_injection_combo",
        )
        self.capture_center_click_assist_combo = QComboBox()
        self.capture_center_click_assist_combo.addItem("On No Movement", "on_no_movement")
        self.capture_center_click_assist_combo.addItem("Off", "off")
        self._assign_control_identity(
            self.capture_center_click_assist_combo,
            "capture_center_click_assist_combo",
            "capture_center_click_assist_combo",
        )
        self.capture_cursor_hold_combo = QComboBox()
        self.capture_cursor_hold_combo.addItem("Keep At Center", "keep_at_center")
        self.capture_cursor_hold_combo.addItem("Restore Each Step", "restore_each_step")
        self._assign_control_identity(
            self.capture_cursor_hold_combo,
            "capture_cursor_hold_combo",
            "capture_cursor_hold_combo",
        )
        self.capture_log_level_combo = QComboBox()
        self.capture_log_level_combo.addItem("INFO", "INFO")
        self.capture_log_level_combo.addItem("DEBUG", "DEBUG")
        self._assign_control_identity(
            self.capture_log_level_combo,
            "capture_log_level_combo",
            "capture_log_level_combo",
        )
        cap_grid.addWidget(QLabel("Max pages"), 0, 0)
        cap_grid.addWidget(self.max_pages_spin, 0, 1)
        cap_grid.addWidget(QLabel("Scroll delay"), 1, 0)
        cap_grid.addWidget(self.capture_delay_spin, 1, 1)
        cap_grid.addWidget(QLabel("Capture backend"), 2, 0)
        cap_grid.addWidget(self.capture_backend_combo, 2, 1)
        cap_grid.addWidget(QLabel("Scroll strategy"), 3, 0)
        cap_grid.addWidget(self.capture_scroll_strategy_combo, 3, 1)
        cap_grid.addWidget(QLabel("Wheel injection"), 4, 0)
        cap_grid.addWidget(self.capture_wheel_injection_combo, 4, 1)
        cap_grid.addWidget(QLabel("Center click assist"), 5, 0)
        cap_grid.addWidget(self.capture_center_click_assist_combo, 5, 1)
        cap_grid.addWidget(QLabel("Cursor hold"), 6, 0)
        cap_grid.addWidget(self.capture_cursor_hold_combo, 6, 1)
        cap_grid.addWidget(QLabel("Diagnostics log level"), 7, 0)
        cap_grid.addWidget(self.capture_log_level_combo, 7, 1)
        cap_grid.addWidget(self.auto_target_checkbox, 8, 0, 1, 2)
        cap_grid.addWidget(self.pick_second_last_button, 9, 0, 1, 2)
        cap_adv_layout.addLayout(cap_grid)
        right_layout.addWidget(self.capture_advanced_group)
        right_layout.addWidget(QLabel("Capture Log"))
        self.capture_log_list = QListWidget(right)
        self.capture_log_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.capture_log_list.setAlternatingRowColors(True)
        self.capture_log_list.setMinimumHeight(140)
        self._assign_control_identity(
            self.capture_log_list,
            "capture_log_list",
            "capture_log_list",
        )
        right_layout.addWidget(self.capture_log_list, 1)
        right_layout.addStretch(1)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.tabs.addTab(capture_tab, "Capture")

        editor_tab = QWidget(self)
        editor_layout = QVBoxLayout(editor_tab)
        self.preview_scroll = QScrollArea()
        self._assign_control_identity(self.preview_scroll, "preview_scroll", "preview_scroll")
        self.preview_scroll.setWidgetResizable(True)
        self.preview_label = QLabel("No capture selected")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_scroll.setWidget(self.preview_label)
        editor_layout.addWidget(self.preview_scroll, stretch=1)
        self.preview_scroll.viewport().installEventFilter(self)

        zoom_row = QHBoxLayout()
        self.zoom_fit_height_button = QPushButton("Fit Height")
        self.zoom_fit_width_button = QPushButton("Fit Width")
        self.zoom_100_button = QPushButton("100%")
        self.zoom_out_button = QPushButton("-")
        self.zoom_in_button = QPushButton("+")
        self.zoom_status_label = QLabel("Fit Height")
        for widget, control in (
            (self.zoom_fit_height_button, "zoom_fit_height_button"),
            (self.zoom_fit_width_button, "zoom_fit_width_button"),
            (self.zoom_100_button, "zoom_100_button"),
            (self.zoom_out_button, "zoom_out_button"),
            (self.zoom_in_button, "zoom_in_button"),
            (self.zoom_status_label, "zoom_status_label"),
        ):
            self._assign_control_identity(widget, control, control)
        zoom_row.addWidget(self.zoom_fit_height_button)
        zoom_row.addWidget(self.zoom_fit_width_button)
        zoom_row.addWidget(self.zoom_100_button)
        zoom_row.addWidget(self.zoom_out_button)
        zoom_row.addWidget(self.zoom_in_button)
        zoom_row.addWidget(self.zoom_status_label, 1)
        editor_layout.addLayout(zoom_row)

        form = QFormLayout()
        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(10.0, 400.0)
        self.zoom_spin.setValue(100.0)
        self.zoom_spin.setSuffix(" %")
        self.rotate_spin = QSpinBox()
        self.rotate_spin.setRange(-180, 180)
        self.straighten_spin = QSpinBox()
        self.straighten_spin.setRange(-15, 15)
        self.auto_crop_button = QPushButton("Suggest Nav Crop")
        self.open_mini_editor_button = QPushButton("Open Mini Editor")
        self.reset_item_edits_button = QPushButton("Reset Item Edits")
        form.addRow("Scale", self.zoom_spin)
        form.addRow("Rotate", self.rotate_spin)
        form.addRow("Straighten", self.straighten_spin)
        form.addRow(self.auto_crop_button)
        form.addRow(self.open_mini_editor_button)
        form.addRow(self.reset_item_edits_button)
        editor_layout.addLayout(form)
        self.editor_advanced_group, edit_adv_layout = self._new_collapsible_group(
            "Split Markers",
            expanded=not self._bool_setting("ui.editor_adv_collapsed", True),
            parent=editor_tab,
        )
        split_form = QFormLayout()
        self.split_spin = QSpinBox()
        self.split_spin.setRange(0, 1000000)
        self.add_split_button = QPushButton("Add Split Marker")
        self.split_list = QListWidget()
        self.remove_split_button = QPushButton("Remove Split Marker")
        self.preview_breaks_button = QPushButton("Preview Breaks")
        split_form.addRow("Split Y", self.split_spin)
        split_form.addRow(self.add_split_button)
        split_form.addRow(self.split_list)
        split_form.addRow(self.remove_split_button)
        split_form.addRow(self.preview_breaks_button)
        edit_adv_layout.addLayout(split_form)
        editor_layout.addWidget(self.editor_advanced_group)
        self.tabs.addTab(editor_tab, "Editor")

        export_tab = QWidget(self)
        export_layout = QVBoxLayout(export_tab)
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
        self._assign_control_identity(self.export_button, "export_button", "export_button")

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
        export_layout.addWidget(export)
        self.export_advanced_group, exp_adv_layout = self._new_collapsible_group(
            "Print Controls",
            expanded=not self._bool_setting("ui.export_adv_collapsed", True),
            parent=export_tab,
        )
        exp_adv_layout.addWidget(QLabel("Print controls are available in the Export tab grid."))
        export_layout.addWidget(self.export_advanced_group)
        self.tabs.addTab(export_tab, "Export")

        self.status_label = QLabel("Ready.")
        self._assign_control_identity(self.status_label, "status_label", "status_label")
        layout.addWidget(self.status_label)

    def _bind_events(self) -> None:
        self.pick_list_button.clicked.connect(self._pick_window_from_list)
        self.pick_crosshair_button.clicked.connect(self._pick_window_crosshair)
        self.pick_second_last_button.clicked.connect(self._pick_second_last_window)
        self.capture_last_selected_button.clicked.connect(self._capture_last_selected_window)
        self.capture_button.clicked.connect(self._capture_selected_viewport)
        self.capture_full_button.clicked.connect(self._capture_full_scroll)
        self.stop_button.clicked.connect(self._request_stop)
        self.import_button.clicked.connect(self._import_images)
        self.quick_export_button.clicked.connect(self._run_export)
        self.queue_list.currentRowChanged.connect(self._on_queue_selection_changed)
        self.up_button.clicked.connect(self._queue_move_up)
        self.down_button.clicked.connect(self._queue_move_down)
        self.remove_button.clicked.connect(self._queue_remove)
        self.auto_crop_button.clicked.connect(self._apply_auto_crop)
        self.open_mini_editor_button.clicked.connect(self._open_mini_editor)
        self.reset_item_edits_button.clicked.connect(self._reset_item_edits)
        self.add_split_button.clicked.connect(self._add_split_marker)
        self.remove_split_button.clicked.connect(self._remove_split_marker)
        self.preview_breaks_button.clicked.connect(self._preview_breaks)
        self.output_browse_button.clicked.connect(self._browse_output)
        self.export_button.clicked.connect(self._run_export)
        self.zoom_spin.valueChanged.connect(self._editor_controls_changed)
        self.rotate_spin.valueChanged.connect(self._editor_controls_changed)
        self.straighten_spin.valueChanged.connect(self._editor_controls_changed)
        self.zoom_fit_height_button.clicked.connect(lambda: self._set_editor_zoom_mode("fit_height"))
        self.zoom_fit_width_button.clicked.connect(lambda: self._set_editor_zoom_mode("fit_width"))
        self.zoom_100_button.clicked.connect(lambda: self._set_editor_zoom_mode("manual", 100))
        self.zoom_out_button.clicked.connect(lambda: self._adjust_editor_zoom(-10))
        self.zoom_in_button.clicked.connect(lambda: self._adjust_editor_zoom(10))
        self.capture_backend_combo.currentIndexChanged.connect(self._persist_capture_backend)
        self.capture_scroll_strategy_combo.currentIndexChanged.connect(
            self._persist_capture_scroll_strategy
        )
        self.capture_wheel_injection_combo.currentIndexChanged.connect(
            self._persist_capture_wheel_injection_mode
        )
        self.capture_center_click_assist_combo.currentIndexChanged.connect(
            self._persist_capture_center_click_assist
        )
        self.capture_cursor_hold_combo.currentIndexChanged.connect(
            self._persist_capture_cursor_hold_mode
        )
        self.capture_log_level_combo.currentIndexChanged.connect(
            self._persist_capture_log_level
        )
        for checkbox in (
            self.pdf_checkbox,
            self.paged_images_checkbox,
            self.long_image_checkbox,
            self.tiff_checkbox,
            self.docx_checkbox,
            self.pptx_checkbox,
        ):
            checkbox.toggled.connect(lambda _value: self._sync_quick_formats_from_main())
        for checkbox in (
            self.quick_pdf_checkbox,
            self.quick_paged_checkbox,
            self.quick_long_checkbox,
            self.quick_tiff_checkbox,
            self.quick_docx_checkbox,
            self.quick_pptx_checkbox,
        ):
            checkbox.toggled.connect(lambda _value: self._sync_main_formats_from_quick())
        self._hotkeys.capture_selected_requested.connect(self._capture_selected_viewport)
        self._hotkeys.capture_full_requested.connect(self._capture_full_scroll)
        self._hotkeys.stop_capture_requested.connect(self._request_stop)
        self._stop_overlay.stop_requested.connect(self._request_stop)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        exit_action = QAction("E&xit", self)
        exit_action.setShortcuts(
            [
                QKeySequence("Ctrl+Q"),
                QKeySequence("Alt+X"),
            ]
        )
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = self.menuBar().addMenu("View")
        settings_action = QAction("Settings", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings_window)
        view_menu.addAction(settings_action)

    def _open_settings_window(self) -> None:
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self)
            self._settings_window.settings_applied.connect(self._apply_settings_payload)
        self._settings_window.set_values(self._collect_settings_payload())
        self._settings_window.show()
        self._settings_window.raise_()
        self._settings_window.activateWindow()

    def _apply_settings_payload(self, payload_obj: object) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        for key, value in payload.items():
            self._settings.setValue(key, value)
        self._settings.sync()
        self._load_runtime_settings()
        self.status_label.setText("Settings applied.")

    def _collect_settings_payload(self) -> dict[str, object]:
        return {
            "capture.max_pages": int(self.max_pages_spin.value()),
            "capture.delay_ms": int(self.capture_delay_spin.value()),
            "capture.auto_pick_second_last": self.auto_target_checkbox.isChecked(),
            "capture.backend_primary": str(self.capture_backend_combo.currentData() or DEFAULT_CAPTURE_BACKEND),
            "capture.scroll_strategy": str(
                self.capture_scroll_strategy_combo.currentData() or DEFAULT_SCROLL_STRATEGY
            ),
            "capture.wheel_injection_mode": str(
                self.capture_wheel_injection_combo.currentData() or DEFAULT_WHEEL_INJECTION_MODE
            ),
            "capture.center_click_assist": str(
                self.capture_center_click_assist_combo.currentData() or DEFAULT_CENTER_CLICK_ASSIST
            ),
            "capture.cursor_hold_mode": str(
                self.capture_cursor_hold_combo.currentData() or DEFAULT_CURSOR_HOLD_MODE
            ),
            "capture.log_level": str(
                self.capture_log_level_combo.currentData() or DEFAULT_CAPTURE_LOG_LEVEL
            ),
            "editor.auto_open_mini": self._bool_setting("editor.auto_open_mini", False),
            "editor.show_grid": self._bool_setting("editor.show_grid", False),
            "export.output_dir": self.output_input.text().strip(),
            "export.basename": self.base_input.text().strip(),
            "export.combine_mode": self.combine_checkbox.isChecked(),
            "export.pdf": self.pdf_checkbox.isChecked(),
            "export.paged_images": self.paged_images_checkbox.isChecked(),
            "export.long_image": self.long_image_checkbox.isChecked(),
            "export.tiff": self.tiff_checkbox.isChecked(),
            "export.docx": self.docx_checkbox.isChecked(),
            "export.pptx": self.pptx_checkbox.isChecked(),
            "export.docx_mode": str(self.docx_mode_combo.currentData()),
            "ui.start_tab": ["capture", "editor", "export"][self.tabs.currentIndex()],
            "ui.editor_adv_collapsed": not self.editor_advanced_group.isChecked(),
            "ui.export_adv_collapsed": not self.export_advanced_group.isChecked(),
        }

    def _load_runtime_settings(self) -> None:
        self.max_pages_spin.setValue(int(self._settings.value("capture.max_pages", 18)))
        self.capture_delay_spin.setValue(int(self._settings.value("capture.delay_ms", 380)))
        self.auto_target_checkbox.setChecked(self._bool_setting("capture.auto_pick_second_last", True))
        backend = str(self._settings.value("capture.backend_primary", DEFAULT_CAPTURE_BACKEND))
        self._set_capture_backend_combo(backend)
        scroll_strategy = str(
            self._settings.value("capture.scroll_strategy", DEFAULT_SCROLL_STRATEGY)
        )
        self._set_scroll_strategy_combo(scroll_strategy)
        wheel_injection = str(
            self._settings.value(
                "capture.wheel_injection_mode",
                DEFAULT_WHEEL_INJECTION_MODE,
            )
        )
        self._set_wheel_injection_combo(wheel_injection)
        center_click_assist = str(
            self._settings.value(
                "capture.center_click_assist",
                DEFAULT_CENTER_CLICK_ASSIST,
            )
        )
        self._set_center_click_assist_combo(center_click_assist)
        cursor_hold_mode = str(
            self._settings.value(
                "capture.cursor_hold_mode",
                DEFAULT_CURSOR_HOLD_MODE,
            )
        )
        self._set_cursor_hold_combo(cursor_hold_mode)
        capture_log_level = normalize_capture_log_level(
            str(self._settings.value("capture.log_level", DEFAULT_CAPTURE_LOG_LEVEL))
        )
        self._set_capture_log_level_combo(capture_log_level)
        self._apply_capture_logger_level(capture_log_level)
        self.combine_checkbox.setChecked(self._bool_setting("export.combine_mode", True))
        self.pdf_checkbox.setChecked(self._bool_setting("export.pdf", True))
        self.paged_images_checkbox.setChecked(self._bool_setting("export.paged_images", False))
        self.long_image_checkbox.setChecked(self._bool_setting("export.long_image", False))
        self.tiff_checkbox.setChecked(self._bool_setting("export.tiff", False))
        self.docx_checkbox.setChecked(self._bool_setting("export.docx", False))
        self.pptx_checkbox.setChecked(self._bool_setting("export.pptx", False))
        mode = str(self._settings.value("export.docx_mode", "per_split_page"))
        if mode == "per_capture":
            self.docx_mode_combo.setCurrentIndex(1)
        else:
            self.docx_mode_combo.setCurrentIndex(0)
        self.base_input.setText(str(self._settings.value("export.basename", self.base_input.text())))
        output_dir = str(self._settings.value("export.output_dir", self.output_input.text()))
        if output_dir:
            self.output_input.setText(output_dir)
        tab_name = str(self._settings.value("ui.start_tab", "capture"))
        self.tabs.setCurrentIndex({"capture": 0, "editor": 1, "export": 2}.get(tab_name, 0))
        self.editor_advanced_group.setChecked(not self._bool_setting("ui.editor_adv_collapsed", True))
        self.export_advanced_group.setChecked(not self._bool_setting("ui.export_adv_collapsed", True))
        self._sync_quick_formats_from_main()
        self._set_editor_zoom_mode("fit_height")

    @staticmethod
    def _row_widget(widgets: list[QWidget]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            layout.addWidget(widget)
        return row

    def _new_collapsible_group(
        self, title: str, *, expanded: bool, parent: QWidget
    ) -> tuple[QGroupBox, QVBoxLayout]:
        group = QGroupBox(title, parent)
        group.setCheckable(True)
        group.setChecked(expanded)
        outer = QVBoxLayout(group)
        body = QWidget(group)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)
        body.setVisible(expanded)
        group.toggled.connect(body.setVisible)
        return group, body_layout

    def _bool_setting(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _set_capture_backend_combo(self, backend: str) -> None:
        normalized = str(backend or "").strip().lower()
        if normalized not in CAPTURE_BACKENDS:
            normalized = DEFAULT_CAPTURE_BACKEND
        for index in range(self.capture_backend_combo.count()):
            if str(self.capture_backend_combo.itemData(index)) == normalized:
                self.capture_backend_combo.setCurrentIndex(index)
                return
        self.capture_backend_combo.setCurrentIndex(0)

    def _capture_backend_primary(self) -> str:
        value = str(self.capture_backend_combo.currentData() or DEFAULT_CAPTURE_BACKEND)
        if value in CAPTURE_BACKENDS:
            return value
        return DEFAULT_CAPTURE_BACKEND

    def _persist_capture_backend(self) -> None:
        self._settings.setValue("capture.backend_primary", self._capture_backend_primary())

    def _set_scroll_strategy_combo(self, strategy: str) -> None:
        normalized = str(strategy or "").strip().lower()
        if normalized not in SCROLL_STRATEGIES:
            normalized = DEFAULT_SCROLL_STRATEGY
        for index in range(self.capture_scroll_strategy_combo.count()):
            if str(self.capture_scroll_strategy_combo.itemData(index)) == normalized:
                self.capture_scroll_strategy_combo.setCurrentIndex(index)
                return
        self.capture_scroll_strategy_combo.setCurrentIndex(0)

    def _capture_scroll_strategy(self) -> str:
        value = str(self.capture_scroll_strategy_combo.currentData() or DEFAULT_SCROLL_STRATEGY)
        if value in SCROLL_STRATEGIES:
            return value
        return DEFAULT_SCROLL_STRATEGY

    def _persist_capture_scroll_strategy(self) -> None:
        self._settings.setValue("capture.scroll_strategy", self._capture_scroll_strategy())

    def _set_wheel_injection_combo(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in WHEEL_INJECTION_MODES:
            normalized = DEFAULT_WHEEL_INJECTION_MODE
        for index in range(self.capture_wheel_injection_combo.count()):
            if str(self.capture_wheel_injection_combo.itemData(index)) == normalized:
                self.capture_wheel_injection_combo.setCurrentIndex(index)
                return
        self.capture_wheel_injection_combo.setCurrentIndex(0)

    def _capture_wheel_injection_mode(self) -> str:
        value = str(
            self.capture_wheel_injection_combo.currentData() or DEFAULT_WHEEL_INJECTION_MODE
        )
        if value in WHEEL_INJECTION_MODES:
            return value
        return DEFAULT_WHEEL_INJECTION_MODE

    def _persist_capture_wheel_injection_mode(self) -> None:
        self._settings.setValue(
            "capture.wheel_injection_mode",
            self._capture_wheel_injection_mode(),
        )

    def _set_center_click_assist_combo(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in CENTER_CLICK_ASSIST_MODES:
            normalized = DEFAULT_CENTER_CLICK_ASSIST
        for index in range(self.capture_center_click_assist_combo.count()):
            if str(self.capture_center_click_assist_combo.itemData(index)) == normalized:
                self.capture_center_click_assist_combo.setCurrentIndex(index)
                return
        self.capture_center_click_assist_combo.setCurrentIndex(0)

    def _capture_center_click_assist(self) -> str:
        value = str(
            self.capture_center_click_assist_combo.currentData() or DEFAULT_CENTER_CLICK_ASSIST
        )
        if value in CENTER_CLICK_ASSIST_MODES:
            return value
        return DEFAULT_CENTER_CLICK_ASSIST

    def _persist_capture_center_click_assist(self) -> None:
        self._settings.setValue(
            "capture.center_click_assist",
            self._capture_center_click_assist(),
        )

    def _set_cursor_hold_combo(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in CURSOR_HOLD_MODES:
            normalized = DEFAULT_CURSOR_HOLD_MODE
        for index in range(self.capture_cursor_hold_combo.count()):
            if str(self.capture_cursor_hold_combo.itemData(index)) == normalized:
                self.capture_cursor_hold_combo.setCurrentIndex(index)
                return
        self.capture_cursor_hold_combo.setCurrentIndex(0)

    def _capture_cursor_hold_mode(self) -> str:
        value = str(self.capture_cursor_hold_combo.currentData() or DEFAULT_CURSOR_HOLD_MODE)
        if value in CURSOR_HOLD_MODES:
            return value
        return DEFAULT_CURSOR_HOLD_MODE

    def _persist_capture_cursor_hold_mode(self) -> None:
        self._settings.setValue(
            "capture.cursor_hold_mode",
            self._capture_cursor_hold_mode(),
        )

    def _set_capture_log_level_combo(self, level: str) -> None:
        normalized = normalize_capture_log_level(level)
        for index in range(self.capture_log_level_combo.count()):
            if str(self.capture_log_level_combo.itemData(index)) == normalized:
                self.capture_log_level_combo.setCurrentIndex(index)
                return
        self.capture_log_level_combo.setCurrentIndex(0)

    def _capture_log_level(self) -> str:
        value = normalize_capture_log_level(
            str(self.capture_log_level_combo.currentData() or DEFAULT_CAPTURE_LOG_LEVEL)
        )
        if value in CAPTURE_LOG_LEVELS:
            return value
        return DEFAULT_CAPTURE_LOG_LEVEL

    def _persist_capture_log_level(self) -> None:
        level = self._capture_log_level()
        self._settings.setValue("capture.log_level", level)
        self._apply_capture_logger_level(level)

    @staticmethod
    def _apply_capture_logger_level(level: str) -> None:
        normalized = normalize_capture_log_level(level)
        capture_level = logging.DEBUG if normalized == "DEBUG" else logging.INFO
        logging.getLogger(CAPTURE_LOGGER_NAME).setLevel(capture_level)

    def _sync_quick_formats_from_main(self) -> None:
        if self._format_sync_guard:
            return
        self._format_sync_guard = True
        try:
            self.quick_pdf_checkbox.setChecked(self.pdf_checkbox.isChecked())
            self.quick_paged_checkbox.setChecked(self.paged_images_checkbox.isChecked())
            self.quick_long_checkbox.setChecked(self.long_image_checkbox.isChecked())
            self.quick_tiff_checkbox.setChecked(self.tiff_checkbox.isChecked())
            self.quick_docx_checkbox.setChecked(self.docx_checkbox.isChecked())
            self.quick_pptx_checkbox.setChecked(self.pptx_checkbox.isChecked())
        finally:
            self._format_sync_guard = False

    def _sync_main_formats_from_quick(self) -> None:
        if self._format_sync_guard:
            return
        self._format_sync_guard = True
        try:
            self.pdf_checkbox.setChecked(self.quick_pdf_checkbox.isChecked())
            self.paged_images_checkbox.setChecked(self.quick_paged_checkbox.isChecked())
            self.long_image_checkbox.setChecked(self.quick_long_checkbox.isChecked())
            self.tiff_checkbox.setChecked(self.quick_tiff_checkbox.isChecked())
            self.docx_checkbox.setChecked(self.quick_docx_checkbox.isChecked())
            self.pptx_checkbox.setChecked(self.quick_pptx_checkbox.isChecked())
        finally:
            self._format_sync_guard = False

    def _apply_start_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width = int(area.width() * 0.76)
        height = int(area.height() * 0.82)
        x_pos = area.left() + (area.width() - width) // 2
        y_pos = area.top() + int(area.height() * 0.08)
        self.setGeometry(x_pos, y_pos, width, height)

    def _load_defaults(self) -> None:
        output_dir = resolve_app_data_dir(APP_IDENTITY) / "captures"
        output_dir.mkdir(parents=True, exist_ok=True)
        self.output_input.setText(str(output_dir))
        self._refresh_queue_summary()

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

    def _pick_second_last_window(self) -> None:
        hwnd = self._capture_service.resolve_second_last_window(int(self.winId()))
        if hwnd is None:
            self.status_label.setText("No second-last active window found.")
            return
        self._set_target(PickedWindow(hwnd=hwnd, label=self._capture_service.window_title(hwnd)))

    def _select_default_browser_target(self) -> None:
        if self._selected_target is not None:
            return
        windows = self._capture_service.list_top_windows(int(self.winId()))
        if not windows:
            return
        process_rank = {name: index for index, name in enumerate(BROWSER_PROCESS_PRIORITY)}
        browser_candidates = [
            item
            for item in windows
            if item.process_name.strip().lower() in process_rank
        ]
        if not browser_candidates:
            return
        best = min(
            browser_candidates,
            key=lambda item: (
                process_rank.get(item.process_name.strip().lower(), len(process_rank)),
                item.label.lower(),
            ),
        )
        self._set_target(PickedWindow(hwnd=best.hwnd, label=best.label))
        self.status_label.setText(
            f"Default browser target selected: {best.label} [hwnd={best.hwnd}]"
        )
        self._append_capture_log(
            f"Default browser target selected ({best.process_name or 'browser'})."
        )

    def _capture_last_selected_window(self) -> None:
        hwnd = self._capture_service.resolve_second_last_window(int(self.winId()))
        if hwnd is None:
            self.status_label.setText("No second-last active window found for quick capture.")
            return
        self._set_target(PickedWindow(hwnd=hwnd, label=self._capture_service.window_title(hwnd)))
        self._capture_selected_viewport()

    def _set_target(self, target: PickedWindow) -> None:
        self._selected_target = target
        self.target_label.setText(f"Target: {target.label} [hwnd={target.hwnd}]")
        if self.base_input.text().strip() in {"", "capture"}:
            self.base_input.setText(sanitize_basename(target.label))
        self.status_label.setText("Target selected.")

    def _capture_selected_viewport(self) -> None:
        if self._selected_target is None and self.auto_target_checkbox.isChecked():
            self._pick_second_last_window()
        if self._selected_target is None:
            self.status_label.setText("Select a target window first, or enable auto-target.")
            return
        focused, message = self._capture_service.activate_window(self._selected_target.hwnd)
        if not focused:
            self.status_label.setText(message)
            return
        pixmap, backend_used = self._capture_service.capture_window(
            self._selected_target.hwnd,
            primary_backend=self._capture_backend_primary(),
        )
        if pixmap is None:
            self.status_label.setText("Capture failed.")
            return
        image = ImageQt.fromqpixmap(pixmap).convert("RGB")
        self._add_capture(image=image, title=self._selected_target.label, source_hwnd=self._selected_target.hwnd, frame_count=1)
        self.status_label.setText(f"Captured selected viewport ({backend_used or 'unknown backend'}).")

    def _capture_full_scroll(self) -> None:
        if self._selected_target is None and self.auto_target_checkbox.isChecked():
            self._pick_second_last_window()
        if self._selected_target is None:
            self.status_label.setText("Select a target window first, or enable auto-target.")
            return
        if self._capture_worker is not None and self._capture_worker.isRunning():
            self.status_label.setText("Capture already running.")
            return
        focused, message = self._capture_service.ensure_window_foreground(
            self._selected_target.hwnd
        )
        if not focused:
            self.status_label.setText(message or "Could not focus selected target window.")
            self._append_capture_log(
                f"Failed to focus target before full capture: {message or 'unknown reason'}"
            )
            return
        self._stop_event.clear()
        self._capture_worker = FullCaptureWorker(
            capture_service=self._capture_service,
            target_hwnd=self._selected_target.hwnd,
            options=ScrollCaptureOptions(
                max_capture_pages=int(self.max_pages_spin.value()),
                delay_ms=int(self.capture_delay_spin.value()),
                capture_backend=self._capture_backend_primary(),
                scroll_strategy=self._capture_scroll_strategy(),
                wheel_injection_mode=self._capture_wheel_injection_mode(),
                center_click_assist=self._capture_center_click_assist(),
                cursor_hold_mode=self._capture_cursor_hold_mode(),
            ),
            stop_event=self._stop_event,
        )
        self._capture_worker.capture_succeeded.connect(self._full_capture_done)
        self._capture_worker.capture_failed.connect(self._full_capture_failed)
        self._capture_worker.capture_progress.connect(self._on_full_capture_progress)
        self._capture_worker.finished.connect(self._full_capture_finished)
        self.capture_log_list.clear()
        self._append_capture_log(
            f"Target focused: {self._selected_target.label} [hwnd={self._selected_target.hwnd}]."
        )
        self._append_capture_log(
            "Full capture started "
            f"(backend={self._capture_backend_primary()}, strategy={self._capture_scroll_strategy()}, "
            f"wheel={self._capture_wheel_injection_mode()}, click_assist={self._capture_center_click_assist()}, "
            f"cursor={self._capture_cursor_hold_mode()})."
        )
        self._stop_overlay.show_top_right()
        self._capture_worker.start()
        self.status_label.setText("Full capture running. Hover red stop badge or press Ctrl+Shift+X.")

    def _request_stop(self) -> None:
        self._stop_event.set()
        self.status_label.setText("Stop requested...")
        self._append_capture_log("Stop requested by user.")

    def _on_full_capture_progress(self, progress_obj: object) -> None:
        if not isinstance(progress_obj, ScrollCaptureProgress):
            return
        diff_text = "n/a" if progress_obj.diff_score is None else f"{progress_obj.diff_score:.2f}"
        status = (
            f"Full capture frame {progress_obj.frame_index}: "
            f"scroll={progress_obj.scroll_method}, backend={progress_obj.backend_used or 'unknown'}, "
            f"diff={diff_text}, repeat={progress_obj.repeated_count}"
        )
        if progress_obj.stop_reason != "running":
            status = f"{status}, stop={progress_obj.stop_reason}"
        self.status_label.setText(status)
        self._append_capture_log(progress_obj.message or status)

    def _full_capture_done(self, result_obj: object) -> None:
        image = getattr(result_obj, "image", None)
        if image is None:
            self.status_label.setText("Invalid full capture result.")
            return
        frame_count = int(getattr(result_obj, "captured_frames", 0))
        self._add_capture(
            image=image,
            title=self._selected_target.label if self._selected_target else "capture",
            source_hwnd=self._selected_target.hwnd if self._selected_target else None,
            frame_count=frame_count,
        )
        stop_reason = str(getattr(result_obj, "stop_reason", "max_pages"))
        summary = (
            f"Full capture complete: {frame_count} frame(s), "
            f"stop={self._describe_stop_reason(stop_reason)}."
        )
        self.status_label.setText(summary)
        self._append_capture_log(summary)

    def _full_capture_failed(self, message: str) -> None:
        self.status_label.setText(f"Full capture failed: {message}")
        self._append_capture_log(f"Full capture failed: {message}")

    def _full_capture_finished(self) -> None:
        self._stop_overlay.hide()
        self._capture_worker = None
        self._restore_focus_after_full_capture()

    def _restore_focus_after_full_capture(self) -> None:
        own_hwnd = int(self.winId())
        self.raise_()
        self.activateWindow()
        focused, message = self._capture_service.ensure_window_foreground(own_hwnd)
        if focused:
            self._append_capture_log("Focus returned to web-pagez-to-pdf.")
            return
        self._append_capture_log(
            f"Could not restore app focus automatically: {message or 'unknown reason'}"
        )

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
        self._sessions.edits_by_item_id[item.item_id] = EditAdjustments()
        list_item = QListWidgetItem(f"{item.title} [{item.image_path.name}]")
        self.queue_list.addItem(list_item)
        self.queue_list.setCurrentRow(self.queue_list.count() - 1)
        self._refresh_queue_summary()
        if self._bool_setting("editor.auto_open_mini", False):
            self._open_mini_editor()

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
        self._refresh_queue_summary()

    def _queue_move_down(self) -> None:
        row = self.queue_list.currentRow()
        if row < 0 or row >= self.queue_list.count() - 1:
            return
        self._queue[row + 1], self._queue[row] = self._queue[row], self._queue[row + 1]
        item = self.queue_list.takeItem(row)
        self.queue_list.insertItem(row + 1, item)
        self.queue_list.setCurrentRow(row + 1)
        self._refresh_queue_summary()

    def _queue_remove(self) -> None:
        row = self.queue_list.currentRow()
        if row < 0:
            return
        removed = self._queue[row]
        self._sessions.edits_by_item_id.pop(removed.item_id, None)
        self.queue_list.takeItem(row)
        self._queue.pop(row)
        self._refresh_preview()
        self._refresh_queue_summary()

    def _current_item(self) -> CaptureItem | None:
        row = self.queue_list.currentRow()
        if row < 0 or row >= len(self._queue):
            return None
        return self._queue[row]

    def _on_queue_selection_changed(self, *_args: object) -> None:
        self._set_editor_zoom_mode("fit_height")
        self._sync_editor_controls()
        self._sync_split_marker_list()
        self._refresh_preview()
        self._refresh_queue_summary()
        item = self._current_item()
        if self._mini_editor is not None and self._mini_editor.isVisible() and item is not None:
            self._mini_editor.bind_item(item, self._session_for_item(item.item_id))

    def _session_for_item(self, item_id: str) -> EditAdjustments:
        existing = self._sessions.edits_by_item_id.get(item_id)
        if existing is None:
            existing = EditAdjustments()
            self._sessions.edits_by_item_id[item_id] = existing
        return existing

    def _current_session(self) -> EditAdjustments | None:
        item = self._current_item()
        if item is None:
            return None
        return self._session_for_item(item.item_id)

    def _editor_controls_changed(self, *_args: object) -> None:
        if self._editor_sync_guard:
            return
        edits = self._current_session()
        if edits is None:
            return
        self._set_scalar_operation(edits, "scale", "percent", int(self.zoom_spin.value()), 100)
        self._set_scalar_operation(edits, "rotate", "degrees", int(self.rotate_spin.value()), 0)
        self._set_scalar_operation(
            edits,
            "straighten",
            "degrees",
            int(self.straighten_spin.value()),
            0,
        )
        self._refresh_preview()
        item = self._current_item()
        if self._mini_editor is not None and item is not None:
            self._mini_editor.update_session(item.item_id, edits)

    def _set_scalar_operation(
        self,
        edits: EditAdjustments,
        op_type: str,
        key: str,
        value: int,
        neutral: int,
    ) -> None:
        if value == neutral:
            edits.remove_operation(op_type)
            return
        edits.set_operation(op_type, {key: value})

    def _sync_editor_controls(self) -> None:
        edits = self._current_session()
        self._editor_sync_guard = True
        try:
            if edits is None:
                self.zoom_spin.setValue(100.0)
                self.rotate_spin.setValue(0)
                self.straighten_spin.setValue(0)
                return
            scale_op = edits.get_operation("scale")
            rotate_op = edits.get_operation("rotate")
            straighten_op = edits.get_operation("straighten")
            self.zoom_spin.setValue(float(scale_op.params.get("percent", 100)) if scale_op else 100.0)
            self.rotate_spin.setValue(int(rotate_op.params.get("degrees", 0)) if rotate_op else 0)
            self.straighten_spin.setValue(
                int(straighten_op.params.get("degrees", 0)) if straighten_op else 0
            )
        finally:
            self._editor_sync_guard = False

    def _sync_split_marker_list(self) -> None:
        self.split_list.clear()
        edits = self._current_session()
        if edits is None:
            return
        for marker in sorted({int(v) for v in edits.split_markers_px if int(v) > 0}):
            self.split_list.addItem(QListWidgetItem(str(marker)))

    def _open_mini_editor(self) -> None:
        item = self._current_item()
        if item is None:
            self.status_label.setText("Select queue item first.")
            return
        if self._mini_editor is None:
            self._mini_editor = MiniEditorWindow(self)
            self._mini_editor.session_changed.connect(self._mini_editor_changed)
        self._mini_editor.bind_item(item, self._session_for_item(item.item_id))
        self._mini_editor.show()
        self._mini_editor.raise_()
        self._mini_editor.activateWindow()

    def _mini_editor_changed(self, item_id: str, session_obj: object) -> None:
        if not isinstance(session_obj, EditAdjustments):
            return
        self._sessions.edits_by_item_id[item_id] = session_obj
        current = self._current_item()
        if current is not None and current.item_id == item_id:
            self._sync_editor_controls()
            self._sync_split_marker_list()
            self._refresh_preview()

    def _reset_item_edits(self) -> None:
        item = self._current_item()
        if item is None:
            return
        self._sessions.edits_by_item_id[item.item_id] = EditAdjustments()
        self._sync_editor_controls()
        self._sync_split_marker_list()
        self._refresh_preview()
        if self._mini_editor is not None:
            self._mini_editor.update_session(item.item_id, self._session_for_item(item.item_id))

    def _refresh_queue_summary(self) -> None:
        selected = self.queue_list.currentRow() + 1 if self.queue_list.currentRow() >= 0 else 0
        self.queue_summary_label.setText(f"Queue: {len(self._queue)} item(s), selected: {selected}")

    def _append_capture_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.capture_log_list.addItem(QListWidgetItem(f"[{stamp}] {text}"))
        self.capture_log_list.scrollToBottom()
        while self.capture_log_list.count() > 300:
            self.capture_log_list.takeItem(0)

    @staticmethod
    def _describe_stop_reason(reason: str) -> str:
        mapping = {
            "user_stop": "user-stop",
            "repeat_detected": "repeated frame detection",
            "max_pages": "max pages reached",
            "capture_failed": "capture failure (partial)",
        }
        return mapping.get(str(reason or "").strip().lower(), "unknown")

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
            zoom_percent=100.0,
            rotate_degrees=0,
            header_rich_text=self.header_input.toHtml(),
            footer_rich_text=self.footer_input.toHtml(),
        )

    def _set_editor_zoom_mode(self, mode: str, manual_percent: int | None = None) -> None:
        normalized = str(mode or "fit_height").strip().lower()
        if normalized not in {"fit_height", "fit_width", "manual"}:
            normalized = "fit_height"
        self._editor_zoom_mode = normalized
        if manual_percent is not None:
            self._editor_manual_zoom_percent = max(10, min(400, int(manual_percent)))
        if self._editor_zoom_mode == "fit_height":
            self.zoom_status_label.setText("Fit Height")
        elif self._editor_zoom_mode == "fit_width":
            self.zoom_status_label.setText("Fit Width")
        else:
            self.zoom_status_label.setText(f"{self._editor_manual_zoom_percent}%")
        self._refresh_preview()

    def _adjust_editor_zoom(self, delta_percent: int) -> None:
        if self._editor_zoom_mode != "manual":
            self._editor_manual_zoom_percent = 100
        self._set_editor_zoom_mode("manual", self._editor_manual_zoom_percent + int(delta_percent))

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if (
            obj is self.preview_scroll.viewport()
            and event.type() == QEvent.Type.Resize
            and self._editor_zoom_mode in {"fit_height", "fit_width"}
        ):
            self._refresh_preview()
        return super().eventFilter(obj, event)

    def _scaled_for_editor_view(self, pixmap: QPixmap) -> QPixmap:
        if pixmap.isNull():
            return pixmap
        viewport = self.preview_scroll.viewport().size()
        target_width = max(1, viewport.width() - 10)
        target_height = max(1, viewport.height() - 10)
        if self._editor_zoom_mode == "fit_height":
            factor = target_height / max(1, pixmap.height())
            target_size = (
                max(1, round(pixmap.width() * factor)),
                max(1, round(pixmap.height() * factor)),
            )
        elif self._editor_zoom_mode == "fit_width":
            factor = target_width / max(1, pixmap.width())
            target_size = (
                max(1, round(pixmap.width() * factor)),
                max(1, round(pixmap.height() * factor)),
            )
        else:
            factor = max(0.1, float(self._editor_manual_zoom_percent) / 100.0)
            target_size = (
                max(1, round(pixmap.width() * factor)),
                max(1, round(pixmap.height() * factor)),
            )
            self.zoom_status_label.setText(f"{self._editor_manual_zoom_percent}%")
        return pixmap.scaled(
            target_size[0],
            target_size[1],
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _refresh_preview(self, *_args: object) -> None:
        item = self._current_item()
        if item is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("No capture selected")
            self.capture_tab_preview_label.setPixmap(QPixmap())
            self.capture_tab_preview_label.setText("No capture selected")
            return
        if not item.image_path.exists():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Capture file missing")
            self.capture_tab_preview_label.setPixmap(QPixmap())
            self.capture_tab_preview_label.setText("Capture file missing")
            return
        image = Image.open(item.image_path).convert("RGB")
        edits = self._session_for_item(item.item_id)
        preview = apply_edit_transform(
            image,
            PrintLayout(zoom_percent=100.0, rotate_degrees=0),
            edits,
        )
        full_pixmap = pil_to_qpixmap(preview)
        self.preview_label.setPixmap(self._scaled_for_editor_view(full_pixmap))
        self.preview_label.setText("")
        thumb = full_pixmap.scaled(
            max(1, self.capture_tab_preview_label.width() - 8),
            max(1, self.capture_tab_preview_label.height() - 8),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.capture_tab_preview_label.setPixmap(thumb)
        self.capture_tab_preview_label.setText("")

    def _apply_auto_crop(self) -> None:
        item = self._current_item()
        if item is None:
            self.status_label.setText("Select queue item first.")
            return
        image = Image.open(item.image_path).convert("RGB")
        left, right = suggest_navigation_crop(image)
        edits = self._session_for_item(item.item_id)
        if left <= 0 and right <= 0:
            edits.remove_operation("nav_auto_crop")
        else:
            edits.set_operation("nav_auto_crop", {"left": left, "right": right})
        self._refresh_preview()
        if self._mini_editor is not None:
            self._mini_editor.update_session(item.item_id, edits)
        self.status_label.setText(f"Suggested crop left={left}px right={right}px")

    def _add_split_marker(self) -> None:
        edits = self._current_session()
        if edits is None:
            self.status_label.setText("Select queue item first.")
            return
        value = int(self.split_spin.value())
        if value <= 0:
            return
        edits.split_markers_px = sorted({*edits.split_markers_px, value})
        self._sync_split_marker_list()
        self._refresh_preview()

    def _remove_split_marker(self) -> None:
        edits = self._current_session()
        if edits is None:
            return
        row = self.split_list.currentRow()
        if row < 0:
            return
        if row >= len(edits.split_markers_px):
            return
        edits.split_markers_px.pop(row)
        self._sync_split_marker_list()
        self._refresh_preview()

    def _split_markers(self) -> list[int]:
        edits = self._current_session()
        if edits is None:
            return []
        return sorted({int(v) for v in edits.split_markers_px if int(v) > 0})

    def _preview_breaks(self) -> None:
        item = self._current_item()
        if item is None:
            self.status_label.setText("Select queue item first.")
            return
        image = Image.open(item.image_path).convert("RGB")
        transformed = apply_edit_transform(
            image,
            self._collect_layout(),
            self._session_for_item(item.item_id),
        )
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
            edits_by_item_id={
                capture.item_id: self._session_for_item(capture.item_id).clone()
                for capture in captures
            },
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
        for key, value in self._collect_settings_payload().items():
            self._settings.setValue(key, value)
        self._settings.sync()
        self._hotkeys.stop()
        self._stop_overlay.hide()
        if self._mini_editor is not None:
            self._mini_editor.close()
        super().closeEvent(event)
