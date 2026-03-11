"""Main application window for capture, editor tweaks, and multi-format export."""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageQt
from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QRectF,
    QSettings,
    QSignalBlocker,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from reportlab.lib.units import mm
from threep_commons.paths import resolve_app_data_dir

from . import widget_naming
from .capture_service import (
    CAPTURE_BACKENDS,
    CAPTURE_FRAME_REGIONS,
    CAPTURE_LOGGER_NAME,
    DEFAULT_CAPTURE_BACKEND,
    DEFAULT_CAPTURE_FRAME_REGION,
    WindowCaptureService,
    WindowInfo,
)
from .constants import APP_DISPLAY_NAME, APP_IDENTITY
from .editor_canvas import EditorCanvas
from .exporters import run_export, sanitize_basename
from .hotkeys import GlobalHotkeyPoller
from .image_processing import (
    DEFAULT_CONTENT_SIZING_MODE,
    PAPER_SIZES,
    apply_edit_transform,
    content_points_per_pixel,
    compute_page_slices,
    normalize_content_sizing_mode,
    pil_to_qpixmap,
    suggest_auto_vertical_border_crop_with_confidence,
)
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
    CURSOR_HOLD_MODES,
    DEFAULT_AUTO_TRIM_FIXED_STRIPS,
    DEFAULT_CAPTURE_LOG_LEVEL,
    DEFAULT_CURSOR_HOLD_MODE,
    DEFAULT_SCROLL_MODE,
    DEFAULT_WHEEL_INJECTION_MODE,
    SCROLL_MODES,
    WHEEL_INJECTION_MODES,
    ScrollCaptureOptions,
    ScrollCaptureProgress,
    normalize_capture_log_level,
    run_full_page_capture,
)
from .settings_window import SettingsWindow
from .stop_overlay import HoverStopOverlay
from .target_picker import CrosshairPickerOverlay, PickedWindow

BROWSER_PROCESS_PRIORITY = (
    "msedge.exe",
    "chrome.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "arc.exe",
)
DEFAULT_SCROLL_TO_TOP_ON_FULL = True
DEFAULT_CROSSHAIR_MAGNIFIER = 12
DEFAULT_CAPTURE_SPLITTER_RIGHT_PANE_PX = 300
DEFAULT_EDITOR_SPLITTER_RIGHT_PANE_PX = 360
THUMBNAIL_MARGIN_CUE_COLOR = (255, 80, 30, 255)
THUMBNAIL_GUTTER_CUE_COLOR = (0, 220, 255, 255)
THUMBNAIL_BORDER_CUE_COLOR = (255, 255, 255, 240)
CAPTURE_UI_LOGGER = logging.getLogger(CAPTURE_LOGGER_NAME)


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

        CAPTURE_UI_LOGGER.info(
            "worker start target_hwnd=%s backend=%s scroll_mode=%s",
            self._target_hwnd,
            self._options.capture_backend,
            self._options.scroll_mode,
        )
        try:
            result = run_full_page_capture(
                service=self._capture_service,
                target_hwnd=self._target_hwnd,
                options=self._options,
                stop_requested=self._stop_event.is_set,
                progress_callback=_emit_progress,
            )
        except Exception as exc:  # pragma: no cover
            CAPTURE_UI_LOGGER.exception("worker failure target_hwnd=%s", self._target_hwnd)
            self.capture_failed.emit(str(exc))
            return
        CAPTURE_UI_LOGGER.info(
            "worker success target_hwnd=%s frames=%s stop_reason=%s",
            self._target_hwnd,
            int(getattr(result, "captured_frames", 0)),
            str(getattr(result, "stop_reason", "")),
        )
        self.capture_succeeded.emit(result)


class ThumbnailPreviewList(QListWidget):
    """Icon grid list with Ctrl+wheel zoom shortcuts."""

    zoom_delta_requested = Signal(int)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = int(event.angleDelta().y())
            if delta != 0:
                self.zoom_delta_requested.emit(10 if delta > 0 else -10)
                event.accept()
                return
        super().wheelEvent(event)


@dataclass(slots=True)
class _EditorHistoryEntry:
    edits_by_item_id: dict[str, EditAdjustments]
    selected_item_id: str | None


class MainWindow(QMainWindow):
    """Tabbed capture/editor/export window with in-tab editor tools."""

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
        self._settings_window: SettingsWindow | None = None
        self._editor_loading = False
        self._current_preview_slices: list[tuple[int, int]] = []
        self._effective_auto_split_markers: list[int] = []
        self._split_action_mode = "none"
        self._undo_history: list[_EditorHistoryEntry] = []
        self._redo_history: list[_EditorHistoryEntry] = []
        self._history_restoring = False
        self._history_limit = 200
        self._undo_action: QAction | None = None
        self._redo_action: QAction | None = None
        self._editor_scroll_to_top_pending = False
        self._editor_preview_debounce_ms = 333
        self._preview_update_pending_transform = False
        self._preview_update_pending_layout = False
        self._pending_transform_item_id: str | None = None
        self._pending_transform_values: tuple[int, int, int, str] | None = None
        self._window_state_restore_in_progress = False
        self._window_state_timer = QTimer(self)
        self._window_state_timer.setSingleShot(True)
        self._window_state_timer.setInterval(300)
        self._window_state_timer.timeout.connect(self._persist_window_state_snapshot)
        self._splitter_sizes_timer = QTimer(self)
        self._splitter_sizes_timer.setSingleShot(True)
        self._splitter_sizes_timer.setInterval(150)
        self._splitter_sizes_timer.timeout.connect(self._persist_splitter_sizes)
        self._preview_update_timer = QTimer(self)
        self._preview_update_timer.setSingleShot(True)
        self._preview_update_timer.timeout.connect(self._flush_debounced_preview_update)
        self._preview_page_hover_overlays: dict[int, QPixmap] = {}
        self._current_content_points_per_pixel = 1.0
        self._build_ui()
        self._bind_events()
        self._apply_start_geometry()
        self._load_defaults()
        self._load_runtime_settings()
        self._select_default_browser_target()
        self._update_history_actions()
        self._hotkeys.start()
        CAPTURE_UI_LOGGER.info("main window initialized hwnd=%s", int(self.winId()))

    def _build_ui(self) -> None:
        self._assign_widget_identity(self, widget_naming.window_widget_id(self.window_id), "window")
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setMinimumSize(1120, 680)
        self._build_menu()
        self._build_capture_action_controls()

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.tabs = QTabWidget(self)
        self._assign_control_identity(self.tabs, "workflow_tabs", "workflow_tabs")
        layout.addWidget(self.tabs, stretch=1)

        capture_tab = QWidget(self)
        capture_layout = QVBoxLayout(capture_tab)
        self.capture_splitter = QSplitter(Qt.Orientation.Horizontal, capture_tab)
        self._assign_control_identity(self.capture_splitter, "capture_splitter", "capture_splitter")
        capture_layout.addWidget(self.capture_splitter, stretch=1)
        left = QWidget(self.capture_splitter)
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
        right = QWidget(self.capture_splitter)
        right_policy = right.sizePolicy()
        right_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        right.setSizePolicy(right_policy)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.capture_target_actions_group = QGroupBox("Target & Actions", right)
        self._assign_control_identity(
            self.capture_target_actions_group,
            "capture_target_actions_group",
            "capture_target_actions_group",
        )
        target_actions_layout = QVBoxLayout(self.capture_target_actions_group)
        target_actions_layout.setContentsMargins(8, 8, 8, 8)
        target_actions_layout.setSpacing(6)
        target_row = QHBoxLayout()
        target_row.addWidget(self.pick_list_button)
        target_row.addWidget(self.target_label, 1)
        target_actions_layout.addLayout(target_row)
        capture_action_row = QHBoxLayout()
        capture_action_row.addWidget(self.capture_button)
        capture_action_row.addWidget(self.capture_full_button)
        capture_action_row.addWidget(self.capture_last_selected_button)
        target_actions_layout.addLayout(capture_action_row)
        import_stop_row = QHBoxLayout()
        import_stop_row.addWidget(self.stop_button)
        import_stop_row.addWidget(self.import_button)
        import_stop_row.addStretch(1)
        target_actions_layout.addLayout(import_stop_row)
        right_layout.addWidget(self.capture_target_actions_group)

        self.capture_thumbnail_group = QGroupBox("Latest / Selected Thumbnail", right)
        self._assign_control_identity(
            self.capture_thumbnail_group,
            "capture_thumbnail_group",
            "capture_thumbnail_group",
        )
        thumbnail_layout = QVBoxLayout(self.capture_thumbnail_group)
        thumbnail_layout.setContentsMargins(8, 8, 8, 8)
        self.capture_tab_preview_label = QLabel("No capture selected", self.capture_thumbnail_group)
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
        thumbnail_layout.addWidget(self.capture_tab_preview_label)
        right_layout.addWidget(self.capture_thumbnail_group)

        self.capture_advanced_group = QGroupBox("Advanced Capture", right)
        cap_adv_layout = QVBoxLayout(self.capture_advanced_group)
        cap_adv_layout.setContentsMargins(8, 8, 8, 8)
        cap_adv_layout.setSpacing(8)
        self.max_pages_spin = QSpinBox()
        self.max_pages_spin.setRange(2, 300)
        self.max_pages_spin.setValue(50)
        self.capture_delay_spin = QSpinBox()
        self.capture_delay_spin.setRange(120, 2000)
        self.capture_delay_spin.setValue(333)
        self.capture_delay_spin.setSuffix(" ms")
        self.capture_backend_combo = QComboBox()
        self.capture_backend_combo.addItem("Screen Region (GDI)", "screen_region_gdi")
        self.capture_backend_combo.addItem("Qt grabWindow", "qt_grab_window")
        self.capture_backend_combo.addItem("PrintWindow", "print_window")
        self._assign_control_identity(
            self.capture_backend_combo,
            "capture_backend_combo",
            "capture_backend_combo",
        )
        self.capture_scroll_mode_combo = QComboBox()
        self.capture_scroll_mode_combo.addItem("Wheel then PageDown", "wheel_then_pagedown")
        self.capture_scroll_mode_combo.addItem("Wheel", "wheel_only")
        self.capture_scroll_mode_combo.addItem("Wheel + Click", "wheel_click")
        self.capture_scroll_mode_combo.addItem("Wheel + PageDown", "wheel_pagedown")
        self.capture_scroll_mode_combo.addItem("Wheel + Click + PageDown", "wheel_click_pagedown")
        self._assign_control_identity(
            self.capture_scroll_mode_combo,
            "capture_scroll_mode_combo",
            "capture_scroll_mode_combo",
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
        self.capture_frame_region_combo = QComboBox()
        self.capture_frame_region_combo.addItem("Client Area (No Border)", "client_area")
        self.capture_frame_region_combo.addItem("Full Window (Border + Title Bar)", "full_window")
        self._assign_control_identity(
            self.capture_frame_region_combo,
            "capture_frame_region_combo",
            "capture_frame_region_combo",
        )
        self.capture_include_mouse_checkbox = QCheckBox("Capture mouse cursor")
        self._assign_control_identity(
            self.capture_include_mouse_checkbox,
            "capture_include_mouse_checkbox",
            "capture_include_mouse_checkbox",
        )
        self.capture_scroll_to_top_checkbox = QCheckBox("Scroll to Top before full capture")
        self._assign_control_identity(
            self.capture_scroll_to_top_checkbox,
            "capture_scroll_to_top_checkbox",
            "capture_scroll_to_top_checkbox",
        )
        self.capture_auto_trim_fixed_checkbox = QCheckBox(
            "Auto-trim fixed top/bottom strips",
        )
        self._assign_control_identity(
            self.capture_auto_trim_fixed_checkbox,
            "capture_auto_trim_fixed_checkbox",
            "capture_auto_trim_fixed_checkbox",
        )

        self.capture_viewport_options_group = QGroupBox("Viewport Capture", self.capture_advanced_group)
        self._assign_control_identity(
            self.capture_viewport_options_group,
            "capture_viewport_options_group",
            "capture_viewport_options_group",
        )
        viewport_layout = QVBoxLayout(self.capture_viewport_options_group)
        viewport_layout.setContentsMargins(8, 8, 8, 8)
        viewport_layout.setSpacing(4)
        self.capture_viewport_hint_label = QLabel(
            "No viewport-only controls. Use Shared / Diagnostics options below.",
            self.capture_viewport_options_group,
        )
        self.capture_viewport_hint_label.setWordWrap(True)
        self._assign_control_identity(
            self.capture_viewport_hint_label,
            "capture_viewport_hint_label",
            "capture_viewport_hint_label",
        )
        viewport_layout.addWidget(self.capture_viewport_hint_label)
        cap_adv_layout.addWidget(self.capture_viewport_options_group)

        self.capture_full_scroll_options_group = QGroupBox("Full-Scroll Capture", self.capture_advanced_group)
        self._assign_control_identity(
            self.capture_full_scroll_options_group,
            "capture_full_scroll_options_group",
            "capture_full_scroll_options_group",
        )
        full_scroll_layout = QFormLayout(self.capture_full_scroll_options_group)
        full_scroll_layout.setContentsMargins(8, 8, 8, 8)
        full_scroll_layout.setSpacing(6)
        full_scroll_layout.addRow("Max pages", self.max_pages_spin)
        full_scroll_layout.addRow("Scroll delay", self.capture_delay_spin)
        full_scroll_layout.addRow("Scroll mode", self.capture_scroll_mode_combo)
        full_scroll_layout.addRow(self.capture_scroll_to_top_checkbox)
        full_scroll_layout.addRow(self.capture_auto_trim_fixed_checkbox)
        full_scroll_layout.addRow("Wheel injection", self.capture_wheel_injection_combo)
        full_scroll_layout.addRow("Cursor hold", self.capture_cursor_hold_combo)
        cap_adv_layout.addWidget(self.capture_full_scroll_options_group)

        self.capture_shared_diagnostics_group = QGroupBox(
            "Shared / Diagnostics",
            self.capture_advanced_group,
        )
        self._assign_control_identity(
            self.capture_shared_diagnostics_group,
            "capture_shared_diagnostics_group",
            "capture_shared_diagnostics_group",
        )
        shared_layout = QFormLayout(self.capture_shared_diagnostics_group)
        shared_layout.setContentsMargins(8, 8, 8, 8)
        shared_layout.setSpacing(6)
        shared_layout.addRow("Capture backend", self.capture_backend_combo)
        shared_layout.addRow("Frame region", self.capture_frame_region_combo)
        shared_layout.addRow(self.capture_include_mouse_checkbox)
        shared_layout.addRow("Diagnostics log level", self.capture_log_level_combo)
        cap_adv_layout.addWidget(self.capture_shared_diagnostics_group)
        right_layout.addWidget(self.capture_advanced_group)

        self.capture_log_group = QGroupBox("Capture Log", right)
        self._assign_control_identity(
            self.capture_log_group,
            "capture_log_group",
            "capture_log_group",
        )
        capture_log_layout = QVBoxLayout(self.capture_log_group)
        capture_log_layout.setContentsMargins(8, 8, 8, 8)
        self.capture_log_list = QListWidget(self.capture_log_group)
        self.capture_log_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.capture_log_list.setAlternatingRowColors(True)
        self.capture_log_list.setMinimumHeight(140)
        self._assign_control_identity(
            self.capture_log_list,
            "capture_log_list",
            "capture_log_list",
        )
        capture_log_layout.addWidget(self.capture_log_list, 1)
        right_layout.addWidget(self.capture_log_group, 1)
        right_layout.addStretch(1)
        self.capture_splitter.setChildrenCollapsible(False)
        self.capture_splitter.setCollapsible(0, False)
        self.capture_splitter.setCollapsible(1, False)
        self.capture_splitter.setStretchFactor(0, 3)
        self.capture_splitter.setStretchFactor(1, 2)
        self.tabs.addTab(capture_tab, "Capture")

        editor_tab = QWidget(self)
        editor_layout = QVBoxLayout(editor_tab)
        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal, editor_tab)
        self._assign_control_identity(self.editor_splitter, "editor_splitter", "editor_splitter")
        editor_layout.addWidget(self.editor_splitter, stretch=1)

        editor_left = QWidget(self.editor_splitter)
        editor_left_layout = QVBoxLayout(editor_left)
        editor_left_layout.setContentsMargins(0, 0, 0, 0)
        editor_left_layout.setSpacing(6)
        editor_left_layout.addWidget(QLabel("Editor Preview"))
        self.editor_canvas = EditorCanvas(editor_left)
        self._assign_control_identity(self.editor_canvas, "editor_canvas", "editor_canvas")
        editor_left_layout.addWidget(self.editor_canvas, stretch=1)

        editor_right = QWidget(self.editor_splitter)
        editor_right_policy = editor_right.sizePolicy()
        editor_right_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        editor_right.setSizePolicy(editor_right_policy)
        editor_right_layout = QVBoxLayout(editor_right)
        editor_right_layout.setContentsMargins(0, 0, 0, 0)
        editor_right_layout.setSpacing(8)

        self.editor_item_label = QLabel("No queue item selected.", editor_right)
        self._assign_control_identity(self.editor_item_label, "editor_item_label", "editor_item_label")
        editor_right_layout.addWidget(self.editor_item_label)

        view_group = QGroupBox("View", editor_right)
        view_layout = QVBoxLayout(view_group)
        self.zoom_fit_height_button = QPushButton("Fit Height", view_group)
        self.zoom_fit_width_button = QPushButton("Fit Width", view_group)
        self.zoom_100_button = QPushButton("100%", view_group)
        self.zoom_out_button = QPushButton("-", view_group)
        self.zoom_in_button = QPushButton("+", view_group)
        self.editor_view_zoom_spin = QSpinBox(view_group)
        self.editor_view_zoom_spin.setRange(10, 400)
        self.editor_view_zoom_spin.setSingleStep(10)
        self.editor_view_zoom_spin.setValue(100)
        self.editor_view_zoom_spin.setSuffix(" %")
        self.zoom_status_label = QLabel("Fit Width", view_group)
        for widget, control in (
            (self.zoom_fit_height_button, "zoom_fit_height_button"),
            (self.zoom_fit_width_button, "zoom_fit_width_button"),
            (self.zoom_100_button, "zoom_100_button"),
            (self.zoom_out_button, "zoom_out_button"),
            (self.zoom_in_button, "zoom_in_button"),
            (self.editor_view_zoom_spin, "editor_view_zoom_spin"),
            (self.zoom_status_label, "zoom_status_label"),
        ):
            self._assign_control_identity(widget, control, control)
        view_top_row = QHBoxLayout()
        view_top_row.addWidget(self.zoom_fit_height_button)
        view_top_row.addWidget(self.zoom_fit_width_button)
        view_top_row.addWidget(self.zoom_100_button)
        view_top_row.addStretch(1)
        view_bottom_row = QHBoxLayout()
        view_bottom_row.addWidget(self.zoom_out_button)
        view_bottom_row.addWidget(self.zoom_in_button)
        view_bottom_row.addWidget(self.editor_view_zoom_spin)
        view_bottom_row.addWidget(self.zoom_status_label, 1)
        view_layout.addLayout(view_top_row)
        view_layout.addLayout(view_bottom_row)
        editor_right_layout.addWidget(view_group)

        tools_group = QGroupBox("Tools", editor_right)
        tools_layout = QVBoxLayout(tools_group)
        self.editor_tool_buttons = QButtonGroup(self)
        self.editor_tool_buttons.setExclusive(True)
        self.pan_tool_button = self._new_editor_tool_button("Pan", "pan", checked=True)
        self.vertical_crop_tool_button = self._new_editor_tool_button(
            "Vertical Border Crop",
            "crop_vertical_band",
        )
        self.vertical_border_crop_button = QPushButton("Auto Vertical Border Crop", tools_group)
        self.rect_crop_tool_button = self._new_editor_tool_button("Rect Crop", "crop_rect")
        self.free_crop_tool_button = self._new_editor_tool_button("Free Crop", "crop_free")
        self.redact_tool_button = self._new_editor_tool_button("Redact", "redact")
        for widget, control in (
            (self.pan_tool_button, "pan_tool_button"),
            (self.vertical_crop_tool_button, "vertical_crop_tool_button"),
            (self.vertical_border_crop_button, "vertical_border_crop_button"),
            (self.rect_crop_tool_button, "rect_crop_tool_button"),
            (self.free_crop_tool_button, "free_crop_tool_button"),
            (self.redact_tool_button, "redact_tool_button"),
        ):
            self._assign_control_identity(widget, control, control)
        tool_modes_top_row = QHBoxLayout()
        for button in (
            self.pan_tool_button,
            self.rect_crop_tool_button,
            self.free_crop_tool_button,
        ):
            tool_modes_top_row.addWidget(button)
        tool_modes_top_row.addStretch(1)
        tool_modes_bottom_row = QHBoxLayout()
        for button in (
            self.vertical_crop_tool_button,
            self.redact_tool_button,
        ):
            tool_modes_bottom_row.addWidget(button)
        tool_modes_bottom_row.addStretch(1)
        tool_action_row = QHBoxLayout()
        tool_action_label = QLabel("One-shot action", tools_group)
        self._assign_control_identity(tool_action_label, "tool_action_label", "tool_action_label")
        tool_action_row.addWidget(tool_action_label)
        tool_action_row.addWidget(self.vertical_border_crop_button)
        tool_action_row.addStretch(1)
        tools_layout.addLayout(tool_modes_top_row)
        tools_layout.addLayout(tool_modes_bottom_row)
        tools_layout.addLayout(tool_action_row)
        editor_right_layout.addWidget(tools_group)

        transform_group = QGroupBox("Transform", editor_right)
        transform_layout = QFormLayout(transform_group)
        self.content_sizing_mode_combo = QComboBox(transform_group)
        self.content_sizing_mode_combo.addItem("Legacy Fit Width", "legacy_fit_width")
        self.content_sizing_mode_combo.addItem("Fit to Page", "fit_to_page")
        self.content_sizing_mode_combo.addItem("Stretch if Smaller", "stretch_if_smaller")
        self.content_sizing_mode_combo.addItem("Original Size", "original_size")
        self.zoom_spin = QDoubleSpinBox(transform_group)
        self.zoom_spin.setRange(10.0, 400.0)
        self.zoom_spin.setValue(100.0)
        self.zoom_spin.setSuffix(" %")
        self.rotate_spin = QSpinBox(transform_group)
        self.rotate_spin.setRange(-180, 180)
        self.straighten_spin = QSpinBox(transform_group)
        self.straighten_spin.setRange(-15, 15)
        self._assign_control_identity(
            self.content_sizing_mode_combo,
            "content_sizing_mode_combo",
            "content_sizing_mode_combo",
        )
        transform_layout.addRow("Sizing", self.content_sizing_mode_combo)
        transform_layout.addRow("Scale", self.zoom_spin)
        transform_layout.addRow("Rotate", self.rotate_spin)
        transform_layout.addRow("Straighten", self.straighten_spin)
        editor_right_layout.addWidget(transform_group)

        self.layout_preview_group = QGroupBox("Page Layout & Print Preview", editor_right)
        self._assign_control_identity(
            self.layout_preview_group,
            "layout_preview_group",
            "layout_preview_group",
        )
        layout_preview_layout = QVBoxLayout(self.layout_preview_group)
        layout_form = QFormLayout()
        layout_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout_form.setFormAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout_form.setHorizontalSpacing(10)
        layout_form.setVerticalSpacing(6)
        self.layout_preview_form = layout_form
        self.paper_combo = QComboBox(self.layout_preview_group)
        for paper_name in sorted(PAPER_SIZES.keys()):
            self.paper_combo.addItem(paper_name)
        self.paper_combo.setCurrentText("A4")
        self.orientation_combo = QComboBox(self.layout_preview_group)
        self.orientation_combo.addItems(["portrait", "landscape"])
        self.margin_top_spin = QDoubleSpinBox(self.layout_preview_group)
        self.margin_bottom_spin = QDoubleSpinBox(self.layout_preview_group)
        self.margin_left_spin = QDoubleSpinBox(self.layout_preview_group)
        self.margin_right_spin = QDoubleSpinBox(self.layout_preview_group)
        self.gutter_spin = QDoubleSpinBox(self.layout_preview_group)
        for spin, value in (
            (self.margin_top_spin, 20.0),
            (self.margin_bottom_spin, 20.0),
            (self.margin_left_spin, 15.0),
            (self.margin_right_spin, 15.0),
            (self.gutter_spin, 0.0),
        ):
            spin.setRange(0.0, 60.0)
            spin.setValue(value)
        self.blank_spin = QSpinBox(self.layout_preview_group)
        self.blank_spin.setRange(0, 255)
        self.blank_spin.setValue(245)
        self.search_spin = QSpinBox(self.layout_preview_group)
        self.search_spin.setRange(20, 2000)
        self.search_spin.setValue(300)
        self.header_input = QTextEdit(self.layout_preview_group)
        self.footer_input = QTextEdit(self.layout_preview_group)
        for widget, control in (
            (self.paper_combo, "paper_combo"),
            (self.orientation_combo, "orientation_combo"),
            (self.margin_top_spin, "margin_top_spin"),
            (self.margin_bottom_spin, "margin_bottom_spin"),
            (self.margin_left_spin, "margin_left_spin"),
            (self.margin_right_spin, "margin_right_spin"),
            (self.gutter_spin, "gutter_spin"),
            (self.blank_spin, "blank_spin"),
            (self.search_spin, "search_spin"),
            (self.header_input, "header_input"),
            (self.footer_input, "footer_input"),
        ):
            self._assign_control_identity(widget, control, control)
        self.layout_paper_orientation_row = self._row_widget(
            [self.paper_combo, self.orientation_combo],
            stretch=True,
        )
        self.layout_margins_gutter_row = self._row_widget(
            [
                self.margin_top_spin,
                self.margin_bottom_spin,
                self.margin_left_spin,
                self.margin_right_spin,
                self.gutter_spin,
            ],
            stretch=True,
        )
        self.layout_blank_search_row = self._row_widget(
            [self.blank_spin, self.search_spin],
            stretch=True,
        )
        layout_form.addRow(
            "Paper / Orientation",
            self.layout_paper_orientation_row,
        )
        layout_form.addRow(
            "Margins + Gutter",
            self.layout_margins_gutter_row,
        )
        layout_form.addRow(
            "Blank / Search",
            self.layout_blank_search_row,
        )
        layout_preview_layout.addLayout(layout_form)
        self.header_label = QLabel("Header", self.layout_preview_group)
        self.footer_label = QLabel("Footer", self.layout_preview_group)
        self._assign_control_identity(self.header_label, "header_label", "header_label")
        self._assign_control_identity(self.footer_label, "footer_label", "footer_label")
        layout_preview_layout.addWidget(self.header_label)
        layout_preview_layout.addWidget(self.header_input)
        layout_preview_layout.addWidget(self.footer_label)
        layout_preview_layout.addWidget(self.footer_input)
        self.editor_overlay_toggle = QCheckBox(
            "Show split/page overlays",
            self.layout_preview_group,
        )
        self.editor_overlay_toggle.setChecked(True)
        self._assign_control_identity(
            self.editor_overlay_toggle,
            "editor_overlay_toggle",
            "editor_overlay_toggle",
        )
        self.thumbnail_zoom_slider = QSlider(Qt.Orientation.Horizontal, self.layout_preview_group)
        self.thumbnail_zoom_slider.setRange(90, 320)
        self.thumbnail_zoom_slider.setSingleStep(10)
        self.thumbnail_zoom_slider.setPageStep(20)
        self.thumbnail_zoom_slider.setValue(150)
        self._assign_control_identity(
            self.thumbnail_zoom_slider,
            "thumbnail_zoom_slider",
            "thumbnail_zoom_slider",
        )
        thumbnail_controls = QWidget(self.layout_preview_group)
        thumbnail_controls_layout = QHBoxLayout(thumbnail_controls)
        thumbnail_controls_layout.setContentsMargins(0, 0, 0, 0)
        thumbnail_controls_layout.setSpacing(8)
        thumbnail_controls_layout.addWidget(self.editor_overlay_toggle)
        thumbnail_controls_layout.addStretch(1)
        thumbnail_controls_layout.addWidget(QLabel("Thumbnail Zoom", thumbnail_controls))
        thumbnail_controls_layout.addWidget(self.thumbnail_zoom_slider, 1)
        layout_preview_layout.addWidget(thumbnail_controls)
        self.page_preview_list = ThumbnailPreviewList(self.layout_preview_group)
        self.page_preview_list.setViewMode(QListView.ViewMode.IconMode)
        self.page_preview_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.page_preview_list.setMovement(QListView.Movement.Static)
        self.page_preview_list.setIconSize(QSize(150, 210))
        self.page_preview_list.setWordWrap(True)
        self.page_preview_list.setSpacing(8)
        self.page_preview_list.setMinimumHeight(220)
        self.page_preview_list.setMouseTracking(True)
        self.page_preview_list.setUniformItemSizes(True)
        self._assign_control_identity(
            self.page_preview_list,
            "page_preview_list",
            "page_preview_list",
        )
        layout_preview_layout.addWidget(self.page_preview_list, 1)
        editor_right_layout.addWidget(self.layout_preview_group)

        self.editor_advanced_group = QGroupBox("Split Markers", editor_right)
        self._assign_control_identity(
            self.editor_advanced_group,
            "editor_advanced_group",
            "editor_advanced_group",
        )
        edit_adv_layout = QVBoxLayout(self.editor_advanced_group)
        split_form = QFormLayout()
        self.add_split_button = QPushButton("Add Split Marker")
        self.split_list = QListWidget()
        self.remove_split_button = QPushButton("Remove Split Marker")
        self.reset_split_markers_button = QPushButton("Reset Split Markers")
        for widget, control in (
            (self.add_split_button, "add_split_button"),
            (self.split_list, "split_list"),
            (self.remove_split_button, "remove_split_button"),
            (self.reset_split_markers_button, "reset_split_markers_button"),
        ):
            self._assign_control_identity(widget, control, control)
        split_form.addRow(self.add_split_button)
        split_form.addRow(self.split_list)
        split_form.addRow(self.remove_split_button)
        split_form.addRow(self.reset_split_markers_button)
        edit_adv_layout.addLayout(split_form)
        editor_right_layout.addWidget(self.editor_advanced_group)

        ops_group = QGroupBox("Operations", editor_right)
        ops_layout = QVBoxLayout(ops_group)
        self.clear_redactions_button = QPushButton("Clear Redactions")
        self.reset_item_edits_button = QPushButton("Reset Item Edits")
        for widget, control in (
            (self.clear_redactions_button, "clear_redactions_button"),
            (self.reset_item_edits_button, "reset_item_edits_button"),
        ):
            self._assign_control_identity(widget, control, control)
        ops_layout.addWidget(self.clear_redactions_button)
        ops_layout.addWidget(self.reset_item_edits_button)
        editor_right_layout.addWidget(ops_group)
        editor_right_layout.addStretch(1)

        self.editor_splitter.setChildrenCollapsible(False)
        self.editor_splitter.setCollapsible(0, False)
        self.editor_splitter.setCollapsible(1, False)
        self.editor_splitter.setStretchFactor(0, 5)
        self.editor_splitter.setStretchFactor(1, 2)
        self.editor_splitter.setOpaqueResize(False)
        self.tabs.addTab(editor_tab, "Editor")

        export_tab = QWidget(self)
        export_layout = QVBoxLayout(export_tab)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setSpacing(8)
        self.combine_checkbox = QCheckBox("Combine queue")
        self.combine_checkbox.setChecked(True)
        self.pdf_checkbox = QCheckBox("PDF")
        self.pdf_checkbox.setChecked(True)
        self.paged_images_checkbox = QCheckBox("Paged PNG")
        self.long_image_checkbox = QCheckBox("Long PNG")
        self.tiff_checkbox = QCheckBox("TIFF")
        self.docx_checkbox = QCheckBox("DOCX")
        self.pptx_checkbox = QCheckBox("PPTX")
        self.xlsx_checkbox = QCheckBox("Excel (XLSX)")
        self.open_after_export_checkbox = QCheckBox("Open file after export")
        self.open_after_export_checkbox.setChecked(True)
        self._assign_control_identity(
            self.open_after_export_checkbox,
            "open_after_export_checkbox",
            "open_after_export_checkbox",
        )
        self.docx_mode_combo = QComboBox()
        self.docx_mode_combo.addItem("Per split-page", "per_split_page")
        self.docx_mode_combo.addItem("Per capture", "per_capture")
        self.base_input = QLineEdit("capture")
        self.output_input = QLineEdit()
        self.output_browse_button = QPushButton("Browse")
        self.export_button = QPushButton("Export")
        self._assign_control_identity(self.export_button, "export_button", "export_button")
        self.export_formats_group = QGroupBox("Formats", export_tab)
        self._assign_control_identity(
            self.export_formats_group,
            "export_formats_group",
            "export_formats_group",
        )
        export_formats_layout = QGridLayout(self.export_formats_group)
        export_formats_layout.addWidget(self.combine_checkbox, 0, 0, 1, 3)
        export_formats_layout.addWidget(self.pdf_checkbox, 1, 0)
        export_formats_layout.addWidget(self.paged_images_checkbox, 1, 1)
        export_formats_layout.addWidget(self.long_image_checkbox, 1, 2)
        export_formats_layout.addWidget(self.tiff_checkbox, 2, 0)
        export_formats_layout.addWidget(self.docx_checkbox, 2, 1)
        export_formats_layout.addWidget(self.pptx_checkbox, 2, 2)
        export_formats_layout.addWidget(self.xlsx_checkbox, 3, 0)
        export_formats_layout.addWidget(self.open_after_export_checkbox, 3, 1, 1, 2)
        export_formats_layout.addWidget(QLabel("DOCX/PPTX/XLSX mode"), 4, 0)
        export_formats_layout.addWidget(self.docx_mode_combo, 4, 1, 1, 2)
        export_layout.addWidget(self.export_formats_group)

        self.export_output_group = QGroupBox("Output", export_tab)
        self._assign_control_identity(
            self.export_output_group,
            "export_output_group",
            "export_output_group",
        )
        export_output_layout = QFormLayout(self.export_output_group)
        export_output_layout.addRow("Base Name", self.base_input)
        export_output_layout.addRow(
            "Output Folder",
            self._row_widget([self.output_input, self.output_browse_button]),
        )
        export_layout.addWidget(self.export_output_group)

        self.export_run_group = QGroupBox("Run Export", export_tab)
        self._assign_control_identity(
            self.export_run_group,
            "export_run_group",
            "export_run_group",
        )
        export_run_layout = QHBoxLayout(self.export_run_group)
        export_run_layout.addStretch(1)
        export_run_layout.addWidget(self.export_button)
        export_layout.addWidget(self.export_run_group)

        self.export_advanced_group, exp_adv_layout = self._new_collapsible_group(
            "Export Notes",
            expanded=not self._bool_setting("ui.export_adv_collapsed", True),
            parent=export_tab,
        )
        exp_adv_layout.addWidget(
            QLabel("Paper/layout preview settings are configured in the Editor tab.")
        )
        export_layout.addWidget(self.export_advanced_group)
        export_layout.addStretch(1)
        self.tabs.addTab(export_tab, "Export")

        self.status_label = QLabel("Ready.")
        self._assign_control_identity(self.status_label, "status_label", "status_label")
        layout.addWidget(self.status_label)
        self._apply_control_tooltips()

    def _bind_events(self) -> None:
        self.pick_target_menu.aboutToShow.connect(self._populate_pick_target_menu)
        self.capture_last_selected_button.clicked.connect(self._capture_last_selected_window)
        self.capture_button.clicked.connect(self._capture_selected_viewport)
        self.capture_full_button.clicked.connect(self._capture_full_scroll)
        self.stop_button.clicked.connect(self._request_stop)
        self.import_button.clicked.connect(self._import_images)
        self.queue_list.currentRowChanged.connect(self._on_queue_selection_changed)
        self.up_button.clicked.connect(self._queue_move_up)
        self.down_button.clicked.connect(self._queue_move_down)
        self.remove_button.clicked.connect(self._queue_remove)
        self.vertical_border_crop_button.clicked.connect(self._run_vertical_border_crop)
        self.clear_redactions_button.clicked.connect(self._clear_redactions)
        self.reset_item_edits_button.clicked.connect(self._reset_item_edits)
        self.add_split_button.clicked.connect(self._arm_add_split_marker)
        self.remove_split_button.clicked.connect(self._arm_remove_split_marker)
        self.reset_split_markers_button.clicked.connect(self._reset_split_markers)
        self.output_browse_button.clicked.connect(self._browse_output)
        self.export_button.clicked.connect(self._run_export)
        self.editor_tool_buttons.buttonClicked.connect(self._on_editor_tool_changed)
        self.editor_canvas.rect_drawn.connect(self._on_editor_rect_drawn)
        self.editor_canvas.free_crop_drawn.connect(self._on_editor_free_crop)
        self.editor_canvas.split_marker_added.connect(self._on_canvas_split_marker_added)
        self.editor_canvas.split_marker_moved.connect(self._on_canvas_split_marker_moved)
        self.editor_canvas.split_marker_removed.connect(self._on_canvas_split_marker_removed)
        self.editor_canvas.zoom_changed.connect(self._on_editor_canvas_zoom_changed)
        self.content_sizing_mode_combo.currentIndexChanged.connect(self._editor_controls_changed)
        self.zoom_spin.valueChanged.connect(self._editor_controls_changed)
        self.rotate_spin.valueChanged.connect(self._editor_controls_changed)
        self.straighten_spin.valueChanged.connect(self._editor_controls_changed)
        self.zoom_fit_height_button.clicked.connect(lambda: self._set_editor_zoom_mode("fit_height"))
        self.zoom_fit_width_button.clicked.connect(lambda: self._set_editor_zoom_mode("fit_width"))
        self.zoom_100_button.clicked.connect(lambda: self._set_editor_zoom_mode("manual", 100))
        self.zoom_out_button.clicked.connect(lambda: self._adjust_editor_zoom(-10))
        self.zoom_in_button.clicked.connect(lambda: self._adjust_editor_zoom(10))
        self.editor_view_zoom_spin.valueChanged.connect(self._on_editor_view_zoom_spin_changed)
        self.paper_combo.currentIndexChanged.connect(self._on_layout_controls_changed)
        self.orientation_combo.currentIndexChanged.connect(self._on_layout_controls_changed)
        self.margin_top_spin.valueChanged.connect(self._on_layout_controls_changed)
        self.margin_bottom_spin.valueChanged.connect(self._on_layout_controls_changed)
        self.margin_left_spin.valueChanged.connect(self._on_layout_controls_changed)
        self.margin_right_spin.valueChanged.connect(self._on_layout_controls_changed)
        self.gutter_spin.valueChanged.connect(self._on_layout_controls_changed)
        self.blank_spin.valueChanged.connect(self._on_layout_controls_changed)
        self.search_spin.valueChanged.connect(self._on_layout_controls_changed)
        self.header_input.textChanged.connect(self._on_layout_controls_changed)
        self.footer_input.textChanged.connect(self._on_layout_controls_changed)
        self.editor_overlay_toggle.toggled.connect(self._on_overlay_visibility_changed)
        self.thumbnail_zoom_slider.valueChanged.connect(self._on_thumbnail_zoom_slider_changed)
        self.page_preview_list.zoom_delta_requested.connect(self._on_thumbnail_zoom_delta_requested)
        self.page_preview_list.currentRowChanged.connect(self._on_page_preview_selected)
        self.page_preview_list.itemEntered.connect(self._on_page_preview_item_hovered)
        self.page_preview_list.viewport().installEventFilter(self)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.capture_splitter.splitterMoved.connect(self._schedule_splitter_sizes_persist)
        self.editor_splitter.splitterMoved.connect(self._schedule_splitter_sizes_persist)
        self.capture_backend_combo.currentIndexChanged.connect(self._persist_capture_backend)
        self.capture_scroll_mode_combo.currentIndexChanged.connect(self._persist_capture_scroll_mode)
        self.capture_frame_region_combo.currentIndexChanged.connect(self._persist_capture_frame_region)
        self.capture_scroll_to_top_checkbox.toggled.connect(self._persist_capture_scroll_to_top_on_full)
        self.capture_auto_trim_fixed_checkbox.toggled.connect(
            self._persist_capture_auto_trim_fixed_strips
        )
        self.capture_wheel_injection_combo.currentIndexChanged.connect(
            self._persist_capture_wheel_injection_mode
        )
        self.capture_cursor_hold_combo.currentIndexChanged.connect(
            self._persist_capture_cursor_hold_mode
        )
        self.capture_include_mouse_checkbox.toggled.connect(
            self._persist_capture_include_mouse_cursor
        )
        self.capture_log_level_combo.currentIndexChanged.connect(
            self._persist_capture_log_level
        )
        self._hotkeys.capture_selected_requested.connect(self._capture_selected_viewport)
        self._hotkeys.capture_full_requested.connect(self._capture_full_scroll)
        self._hotkeys.stop_capture_requested.connect(self._request_stop)
        self._stop_overlay.stop_requested.connect(self._request_stop)

    def _build_capture_action_controls(self) -> None:
        self.target_label = QLabel("Target: none")
        self.pick_list_button = QPushButton("Pick")
        self.pick_target_menu = QMenu(self.pick_list_button)
        self.capture_button = QPushButton("Capture")
        self.capture_full_button = QPushButton("Capture Full")
        self.capture_last_selected_button = QPushButton("Capture Last Selected")
        self.stop_button = QPushButton("Stop")
        self.import_button = QPushButton("Import")

        for widget, control in (
            (self.target_label, "target_label"),
            (self.pick_list_button, "pick_list_button"),
            (self.capture_button, "capture_button"),
            (self.capture_full_button, "capture_full_button"),
            (self.capture_last_selected_button, "capture_last_selected_button"),
            (self.stop_button, "stop_button"),
            (self.import_button, "import_button"),
        ):
            self._assign_control_identity(widget, control, control)

        self.pick_list_button.setMenu(self.pick_target_menu)

    @staticmethod
    def _set_tooltip(widget: QWidget, text: str) -> None:
        widget.setToolTip(text)
        widget.setStatusTip(text)

    def _apply_control_tooltips(self) -> None:
        tooltip_map: list[tuple[QWidget, str]] = [
            (self.pick_list_button, "Choose the window to capture."),
            (
                self.capture_button,
                "Capture the currently visible viewport of the selected window (Ctrl+Shift+C).",
            ),
            (
                self.capture_full_button,
                "Capture a full scrolling page from the selected window (Ctrl+Shift+S).",
            ),
            (self.capture_last_selected_button, "Capture the last window you selected in the OS."),
            (self.stop_button, "Stop an active full-page capture (Ctrl+Shift+X)."),
            (self.import_button, "Import existing image files into the queue."),
            (self.queue_list, "Queue of captured/imported images used by Editor and Export."),
            (self.up_button, "Move the selected queue item up."),
            (self.down_button, "Move the selected queue item down."),
            (self.remove_button, "Remove the selected queue item from the queue."),
            (self.max_pages_spin, "Maximum pages captured during a full-scroll run."),
            (self.capture_delay_spin, "Delay between scroll steps during full capture."),
            (self.capture_backend_combo, "Primary backend used to grab image frames."),
            (self.capture_scroll_mode_combo, "Scroll automation strategy used for full capture."),
            (self.capture_scroll_to_top_checkbox, "Try to jump to the top before full capture starts."),
            (self.capture_auto_trim_fixed_checkbox, "Auto-trim repeated fixed top/bottom strips after capture."),
            (self.capture_frame_region_combo, "Choose client-only or full-window capture region."),
            (self.capture_wheel_injection_combo, "How wheel input is injected into the target window."),
            (self.capture_cursor_hold_combo, "How the mouse cursor is positioned while scrolling."),
            (self.capture_log_level_combo, "Capture diagnostics log verbosity."),
            (self.capture_include_mouse_checkbox, "Include the mouse cursor in captured frames."),
            (self.capture_log_list, "Capture diagnostics log entries."),
            (self.editor_canvas, "Preview image. Use tools to crop/redact/split directly on the image."),
            (self.zoom_fit_height_button, "Fit the preview to the available viewport height."),
            (self.zoom_fit_width_button, "Fit the preview to the available viewport width."),
            (self.zoom_100_button, "Show preview at 100% zoom."),
            (self.zoom_out_button, "Zoom out by 10%."),
            (self.zoom_in_button, "Zoom in by 10%."),
            (self.editor_view_zoom_spin, "Manual editor view zoom percent."),
            (self.pan_tool_button, "Persistent tool mode: pan/scroll the preview while left mouse is pressed."),
            (
                self.vertical_crop_tool_button,
                "Persistent tool mode: draw a manual vertical crop band for the selected item.",
            ),
            (self.rect_crop_tool_button, "Persistent tool mode: draw a rectangular crop area."),
            (
                self.free_crop_tool_button,
                "Persistent tool mode: draw free-form crop points, then double-click to apply.",
            ),
            (self.redact_tool_button, "Persistent tool mode: draw redaction rectangles."),
            (
                self.content_sizing_mode_combo,
                "Persistent transform mode for print sizing: Legacy Fit Width keeps old behavior; "
                "Fit to Page scales proportionally inside printable area; Stretch if Smaller upscales only when both "
                "dimensions are smaller; Original Size uses 96-DPI native size without auto scaling.",
            ),
            (self.zoom_spin, "Scale transform applied before page slicing/export."),
            (self.rotate_spin, "Rotate image in whole degrees."),
            (self.straighten_spin, "Fine rotation used for straightening."),
            (self.paper_combo, "Target paper size for pagination and export."),
            (self.orientation_combo, "Target page orientation."),
            (self.margin_top_spin, "Top page margin in millimeters."),
            (self.margin_bottom_spin, "Bottom page margin in millimeters."),
            (self.margin_left_spin, "Left page margin in millimeters."),
            (self.margin_right_spin, "Right page margin in millimeters."),
            (self.gutter_spin, "Extra inner gutter margin in millimeters."),
            (
                self.blank_spin,
                "Blank-row threshold (0..255). Higher values treat near-white rows as blank more aggressively; "
                "lower values require cleaner white rows before accepting a split cut.",
            ),
            (
                self.search_spin,
                "Search window in pixels around each ideal page break. Larger windows can find cleaner blank-row "
                "cuts farther from the ideal split; smaller windows keep cuts closer to the target position.",
            ),
            (self.header_input, "Header rich text. Supports tokens like {title}, {page}, {pages}, {datetime}."),
            (self.footer_input, "Footer rich text. Supports tokens like {title}, {page}, {pages}, {datetime}."),
            (self.editor_overlay_toggle, "Toggle page-break guides, split markers, labels, and printable area guides."),
            (self.thumbnail_zoom_slider, "Scale the bottom thumbnail preview row."),
            (self.page_preview_list, "Live page thumbnails generated from current edit and layout settings."),
            (
                self.add_split_button,
                "Arm one-shot split add mode. Then click the image area or Y ruler to place a split marker.",
            ),
            (self.split_list, "Current split markers for this queue item (auto-calculated when no edits are persisted)."),
            (
                self.remove_split_button,
                "Arm one-shot split remove mode. Then click a split marker on the image or Y ruler to remove it.",
            ),
            (
                self.reset_split_markers_button,
                "Clear persisted split marker edits and restore auto-calculated split markers.",
            ),
            (
                self.vertical_border_crop_button,
                "One-shot action: auto-detect left/right content boundaries for the selected queue item and apply a non-destructive vertical border crop.",
            ),
            (self.clear_redactions_button, "One-shot action: remove all redactions for the selected item."),
            (self.reset_item_edits_button, "One-shot action: reset all editor operations for the selected item."),
            (self.combine_checkbox, "Export all queue items as one combined job."),
            (self.pdf_checkbox, "Export PDF output."),
            (self.paged_images_checkbox, "Export one PNG file per computed page slice."),
            (self.long_image_checkbox, "Export one long stitched PNG image."),
            (self.tiff_checkbox, "Export one multi-page TIFF."),
            (self.docx_checkbox, "Export DOCX output."),
            (self.pptx_checkbox, "Export PPTX output."),
            (self.xlsx_checkbox, "Export XLSX output with metadata and image previews."),
            (
                self.open_after_export_checkbox,
                "Open output file (or folder when multiple files are generated) after export.",
            ),
            (self.docx_mode_combo, "Choose whether DOCX/PPTX/XLSX uses split pages or per-capture images."),
            (self.base_input, "Base filename used for exported files."),
            (self.output_input, "Destination folder for exported files."),
            (self.output_browse_button, "Choose destination folder."),
            (self.export_button, "Run export for the selected formats."),
        ]
        for widget, tip in tooltip_map:
            self._set_tooltip(widget, tip)

    def _new_editor_tool_button(self, text: str, tool: str, *, checked: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setProperty("tool", tool)
        self.editor_tool_buttons.addButton(button)
        return button

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

        edit_menu = self.menuBar().addMenu("Edit")
        self._undo_action = QAction("Undo", self)
        self._undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        self._undo_action.triggered.connect(self._undo_editor_change)
        edit_menu.addAction(self._undo_action)
        self._redo_action = QAction("Redo", self)
        self._redo_action.setShortcut(QKeySequence("Ctrl+Y"))
        self._redo_action.triggered.connect(self._redo_editor_change)
        edit_menu.addAction(self._redo_action)

        view_menu = self.menuBar().addMenu("View")
        settings_action = QAction("Settings", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings_window)
        view_menu.addAction(settings_action)
        reset_view_action = QAction("Reset View", self)
        reset_view_action.triggered.connect(self._reset_view_state)
        view_menu.addAction(reset_view_action)

    def _open_settings_window(self) -> None:
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self)
            self._settings_window.settings_applied.connect(self._apply_settings_payload)
        self._settings_window.set_values(self._collect_settings_payload())
        self._settings_window.show()
        self._settings_window.raise_()
        self._settings_window.activateWindow()

    def _apply_settings_payload(self, payload_obj: object) -> None:
        self._flush_debounced_preview_update()
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
            "capture.backend_primary": str(self.capture_backend_combo.currentData() or DEFAULT_CAPTURE_BACKEND),
            "capture.scroll_mode": str(
                self.capture_scroll_mode_combo.currentData() or DEFAULT_SCROLL_MODE
            ),
            "capture.scroll_to_top_on_full": self.capture_scroll_to_top_checkbox.isChecked(),
            "capture.auto_trim_fixed_strips": self.capture_auto_trim_fixed_checkbox.isChecked(),
            "capture.frame_region": str(
                self.capture_frame_region_combo.currentData() or DEFAULT_CAPTURE_FRAME_REGION
            ),
            "capture.wheel_injection_mode": str(
                self.capture_wheel_injection_combo.currentData() or DEFAULT_WHEEL_INJECTION_MODE
            ),
            "capture.cursor_hold_mode": str(
                self.capture_cursor_hold_combo.currentData() or DEFAULT_CURSOR_HOLD_MODE
            ),
            "capture.include_mouse_cursor": self.capture_include_mouse_checkbox.isChecked(),
            "capture.log_level": str(
                self.capture_log_level_combo.currentData() or DEFAULT_CAPTURE_LOG_LEVEL
            ),
            "export.output_dir": self.output_input.text().strip(),
            "export.basename": self.base_input.text().strip(),
            "export.combine_mode": self.combine_checkbox.isChecked(),
            "export.pdf": self.pdf_checkbox.isChecked(),
            "export.paged_images": self.paged_images_checkbox.isChecked(),
            "export.long_image": self.long_image_checkbox.isChecked(),
            "export.tiff": self.tiff_checkbox.isChecked(),
            "export.docx": self.docx_checkbox.isChecked(),
            "export.pptx": self.pptx_checkbox.isChecked(),
            "export.xlsx": self.xlsx_checkbox.isChecked(),
            "export.open_after_export": self.open_after_export_checkbox.isChecked(),
            "export.docx_mode": str(self.docx_mode_combo.currentData()),
            "layout.paper_name": self.paper_combo.currentText(),
            "layout.orientation": self.orientation_combo.currentText(),
            "layout.margin_top_mm": float(self.margin_top_spin.value()),
            "layout.margin_bottom_mm": float(self.margin_bottom_spin.value()),
            "layout.margin_left_mm": float(self.margin_left_spin.value()),
            "layout.margin_right_mm": float(self.margin_right_spin.value()),
            "layout.gutter_mm": float(self.gutter_spin.value()),
            "layout.blank_row_threshold": int(self.blank_spin.value()),
            "layout.search_window_px": int(self.search_spin.value()),
            "layout.header_html": self.header_input.toHtml(),
            "layout.footer_html": self.footer_input.toHtml(),
            "ui.export_adv_collapsed": not self.export_advanced_group.isChecked(),
            "ui.editor_overlay_visible": self.editor_overlay_toggle.isChecked(),
            "editor.preview_debounce_ms": int(self._editor_preview_debounce_ms),
            "ui.window_geometry": self.saveGeometry(),
            "ui.window_is_maximized": self.isMaximized(),
            "ui.capture_splitter_sizes": self.capture_splitter.sizes(),
            "ui.editor_splitter_sizes": self.editor_splitter.sizes(),
        }

    def _load_runtime_settings(self) -> None:
        self._editor_loading = True
        try:
            self.max_pages_spin.setValue(int(self._settings.value("capture.max_pages", 50)))
            self.capture_delay_spin.setValue(int(self._settings.value("capture.delay_ms", 333)))
            backend = str(self._settings.value("capture.backend_primary", DEFAULT_CAPTURE_BACKEND))
            self._set_capture_backend_combo(backend)
            scroll_mode = str(self._settings.value("capture.scroll_mode", DEFAULT_SCROLL_MODE))
            self._set_scroll_mode_combo(scroll_mode)
            self.capture_scroll_to_top_checkbox.setChecked(
                self._bool_setting("capture.scroll_to_top_on_full", DEFAULT_SCROLL_TO_TOP_ON_FULL)
            )
            self.capture_auto_trim_fixed_checkbox.setChecked(
                self._bool_setting(
                    "capture.auto_trim_fixed_strips",
                    DEFAULT_AUTO_TRIM_FIXED_STRIPS,
                )
            )
            if self._settings.contains("capture.auto_trim_scrollbar"):
                self._settings.remove("capture.auto_trim_scrollbar")
            frame_region = str(
                self._settings.value("capture.frame_region", DEFAULT_CAPTURE_FRAME_REGION)
            )
            self._set_capture_frame_region_combo(frame_region)
            wheel_injection = str(
                self._settings.value(
                    "capture.wheel_injection_mode",
                    DEFAULT_WHEEL_INJECTION_MODE,
                )
            )
            self._set_wheel_injection_combo(wheel_injection)
            cursor_hold_mode = str(
                self._settings.value(
                    "capture.cursor_hold_mode",
                    DEFAULT_CURSOR_HOLD_MODE,
                )
            )
            self._set_cursor_hold_combo(cursor_hold_mode)
            self.capture_include_mouse_checkbox.setChecked(
                self._bool_setting("capture.include_mouse_cursor", False)
            )
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
            self.xlsx_checkbox.setChecked(self._bool_setting("export.xlsx", False))
            self.open_after_export_checkbox.setChecked(
                self._bool_setting("export.open_after_export", True)
            )
            mode = str(self._settings.value("export.docx_mode", "per_split_page"))
            if mode == "per_capture":
                self.docx_mode_combo.setCurrentIndex(1)
            else:
                self.docx_mode_combo.setCurrentIndex(0)
            self.base_input.setText(
                str(self._settings.value("export.basename", self.base_input.text()))
            )
            output_dir = str(self._settings.value("export.output_dir", self.output_input.text()))
            if output_dir:
                self.output_input.setText(output_dir)

            paper_name = str(self._settings.value("layout.paper_name", "A4"))
            if self.paper_combo.findText(paper_name) >= 0:
                self.paper_combo.setCurrentText(paper_name)
            orientation = str(self._settings.value("layout.orientation", "portrait"))
            if self.orientation_combo.findText(orientation) >= 0:
                self.orientation_combo.setCurrentText(orientation)
            self.margin_top_spin.setValue(float(self._settings.value("layout.margin_top_mm", 20.0)))
            self.margin_bottom_spin.setValue(
                float(self._settings.value("layout.margin_bottom_mm", 20.0))
            )
            self.margin_left_spin.setValue(float(self._settings.value("layout.margin_left_mm", 15.0)))
            self.margin_right_spin.setValue(
                float(self._settings.value("layout.margin_right_mm", 15.0))
            )
            self.gutter_spin.setValue(float(self._settings.value("layout.gutter_mm", 0.0)))
            self.blank_spin.setValue(int(self._settings.value("layout.blank_row_threshold", 245)))
            self.search_spin.setValue(int(self._settings.value("layout.search_window_px", 300)))
            header_html = str(self._settings.value("layout.header_html", ""))
            footer_html = str(self._settings.value("layout.footer_html", ""))
            self.header_input.setHtml(header_html)
            self.footer_input.setHtml(footer_html)
            overlay_visible = self._bool_setting("ui.editor_overlay_visible", True)
            self.editor_overlay_toggle.setChecked(overlay_visible)
            self.editor_canvas.set_overlay_visibility(overlay_visible)
            self._apply_thumbnail_icon_size(self.thumbnail_zoom_slider.value())
            self._apply_magnifier_visibility_for_tool()
            self._editor_preview_debounce_ms = self._clamp_preview_debounce_ms(
                self._settings.value("editor.preview_debounce_ms", 333)
            )
            self._preview_update_timer.setInterval(self._editor_preview_debounce_ms)
            self._restore_splitter_sizes()
            QTimer.singleShot(0, self._enforce_default_splitter_sizes_if_unsaved)

            self.tabs.setCurrentIndex(0)
            self.export_advanced_group.setChecked(
                not self._bool_setting("ui.export_adv_collapsed", True)
            )
            self._set_editor_zoom_mode("fit_width")
        finally:
            self._editor_loading = False

    @staticmethod
    def _row_widget(widgets: list[QWidget], *, stretch: bool = False) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for widget in widgets:
            layout.addWidget(widget, 1 if stretch else 0)
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

    def _int_list_setting(self, key: str) -> list[int]:
        value = self._settings.value(key)
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            raw_values = list(value)
        elif isinstance(value, str):
            raw_values = [part for part in re.split(r"[,\s]+", value.strip()) if part]
        else:
            raw_values = [value]
        parsed: list[int] = []
        for raw in raw_values:
            try:
                number = int(float(raw))
            except (TypeError, ValueError):
                continue
            if number > 0:
                parsed.append(number)
        return parsed

    @staticmethod
    def _clamp_preview_debounce_ms(value: object) -> int:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            parsed = 333
        return max(0, min(2000, parsed))

    def _persist_splitter_sizes(self, *_args: object) -> None:
        self._settings.setValue("ui.capture_splitter_sizes", self.capture_splitter.sizes())
        self._settings.setValue("ui.editor_splitter_sizes", self.editor_splitter.sizes())

    def _schedule_splitter_sizes_persist(self, *_args: object) -> None:
        self._splitter_sizes_timer.start()

    @staticmethod
    def _set_splitter_right_pane_width(splitter: QSplitter, right_width_px: int) -> None:
        total_width = int(splitter.width())
        if total_width <= 0:
            sizes = splitter.sizes()
            total_width = int(sum(sizes))
        if total_width <= 0:
            total_width = int(right_width_px) * 3
        right = max(120, min(int(right_width_px), max(120, total_width - 120)))
        handle_pos = max(120, total_width - right)
        splitter.moveSplitter(handle_pos, 1)

    def _apply_capture_splitter_default(self) -> None:
        self._set_splitter_right_pane_width(
            self.capture_splitter,
            DEFAULT_CAPTURE_SPLITTER_RIGHT_PANE_PX,
        )

    def _apply_editor_splitter_default(self) -> None:
        self._set_splitter_right_pane_width(
            self.editor_splitter,
            DEFAULT_EDITOR_SPLITTER_RIGHT_PANE_PX,
        )

    def _apply_default_splitter_sizes(self) -> None:
        self._apply_capture_splitter_default()
        self._apply_editor_splitter_default()

    def _enforce_default_splitter_sizes_if_unsaved(self) -> None:
        if len(self._int_list_setting("ui.capture_splitter_sizes")) < 2:
            self._apply_capture_splitter_default()
        if (
            self.tabs.currentIndex() == 1
            and len(self._int_list_setting("ui.editor_splitter_sizes")) < 2
        ):
            self._apply_editor_splitter_default()

    def _restore_splitter_sizes(self) -> None:
        capture_sizes = self._int_list_setting("ui.capture_splitter_sizes")
        if len(capture_sizes) >= 2:
            self.capture_splitter.setSizes(capture_sizes)
        else:
            self._set_splitter_right_pane_width(
                self.capture_splitter,
                DEFAULT_CAPTURE_SPLITTER_RIGHT_PANE_PX,
            )

        editor_sizes = self._int_list_setting("ui.editor_splitter_sizes")
        if len(editor_sizes) >= 2:
            self.editor_splitter.setSizes(editor_sizes)
        else:
            self.editor_splitter.setSizes([980, DEFAULT_EDITOR_SPLITTER_RIGHT_PANE_PX])

    def _reset_view_state(self) -> None:
        keys = (
            "ui.window_geometry",
            "ui.window_is_maximized",
            "ui.capture_splitter_sizes",
            "ui.editor_splitter_sizes",
        )
        for key in keys:
            self._settings.remove(key)
        self._settings.sync()
        self._window_state_restore_in_progress = True
        try:
            self.showNormal()
            self._apply_default_splitter_sizes()
            self.showMaximized()
            self._apply_default_splitter_sizes()
            QTimer.singleShot(50, self._apply_default_splitter_sizes)
        finally:
            self._window_state_restore_in_progress = False
        self._persist_window_state_snapshot()
        self.status_label.setText("View reset to defaults.")

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

    def _set_scroll_mode_combo(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in SCROLL_MODES:
            normalized = DEFAULT_SCROLL_MODE
        for index in range(self.capture_scroll_mode_combo.count()):
            if str(self.capture_scroll_mode_combo.itemData(index)) == normalized:
                self.capture_scroll_mode_combo.setCurrentIndex(index)
                return
        self.capture_scroll_mode_combo.setCurrentIndex(0)

    def _capture_scroll_mode(self) -> str:
        value = str(self.capture_scroll_mode_combo.currentData() or DEFAULT_SCROLL_MODE)
        if value in SCROLL_MODES:
            return value
        return DEFAULT_SCROLL_MODE

    def _persist_capture_scroll_mode(self) -> None:
        self._settings.setValue("capture.scroll_mode", self._capture_scroll_mode())

    def _set_capture_frame_region_combo(self, frame_region: str) -> None:
        normalized = str(frame_region or "").strip().lower()
        if normalized not in CAPTURE_FRAME_REGIONS:
            normalized = DEFAULT_CAPTURE_FRAME_REGION
        for index in range(self.capture_frame_region_combo.count()):
            if str(self.capture_frame_region_combo.itemData(index)) == normalized:
                self.capture_frame_region_combo.setCurrentIndex(index)
                return
        self.capture_frame_region_combo.setCurrentIndex(0)

    def _capture_frame_region(self) -> str:
        value = str(self.capture_frame_region_combo.currentData() or DEFAULT_CAPTURE_FRAME_REGION)
        if value in CAPTURE_FRAME_REGIONS:
            return value
        return DEFAULT_CAPTURE_FRAME_REGION

    def _persist_capture_frame_region(self) -> None:
        self._settings.setValue("capture.frame_region", self._capture_frame_region())

    def _capture_include_mouse_cursor(self) -> bool:
        return bool(self.capture_include_mouse_checkbox.isChecked())

    def _capture_scroll_to_top_on_full(self) -> bool:
        return bool(self.capture_scroll_to_top_checkbox.isChecked())

    def _persist_capture_scroll_to_top_on_full(self) -> None:
        self._settings.setValue(
            "capture.scroll_to_top_on_full",
            self._capture_scroll_to_top_on_full(),
        )

    def _capture_auto_trim_fixed_strips(self) -> bool:
        return bool(self.capture_auto_trim_fixed_checkbox.isChecked())

    def _persist_capture_auto_trim_fixed_strips(self) -> None:
        self._settings.setValue(
            "capture.auto_trim_fixed_strips",
            self._capture_auto_trim_fixed_strips(),
        )

    def _persist_capture_include_mouse_cursor(self) -> None:
        self._settings.setValue(
            "capture.include_mouse_cursor",
            self._capture_include_mouse_cursor(),
        )

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

    def _apply_start_geometry(self) -> None:
        self._window_state_restore_in_progress = True
        try:
            restored = False
            raw_geometry = self._settings.value("ui.window_geometry")
            if isinstance(raw_geometry, QByteArray):
                restored = self.restoreGeometry(raw_geometry)
            elif isinstance(raw_geometry, (bytes, bytearray)):
                restored = self.restoreGeometry(QByteArray(bytes(raw_geometry)))
            elif isinstance(raw_geometry, str) and raw_geometry.strip():
                with suppress(Exception):
                    restored = self.restoreGeometry(
                        QByteArray.fromBase64(raw_geometry.encode("ascii", errors="ignore"))
                    )

            if restored:
                if self._bool_setting("ui.window_is_maximized", False):
                    self.showMaximized()
                return
            self.showMaximized()
        finally:
            self._window_state_restore_in_progress = False

    def _schedule_window_state_snapshot(self) -> None:
        if self._window_state_restore_in_progress:
            return
        self._window_state_timer.start()

    def _persist_window_state_snapshot(self) -> None:
        if self._window_state_restore_in_progress:
            return
        self._splitter_sizes_timer.stop()
        self._settings.setValue("ui.window_geometry", self.saveGeometry())
        self._settings.setValue("ui.window_is_maximized", self.isMaximized())
        self._persist_splitter_sizes()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._schedule_window_state_snapshot()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_window_state_snapshot()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._schedule_window_state_snapshot()

    def eventFilter(self, watched: object, event: object) -> bool:
        if (
            watched is self.page_preview_list.viewport()
            and isinstance(event, QEvent)
            and event.type() == QEvent.Type.Leave
        ):
            self._clear_page_preview_hover_overlay()
        return super().eventFilter(watched, event)

    def _load_defaults(self) -> None:
        output_dir = resolve_app_data_dir(APP_IDENTITY) / "captures"
        output_dir.mkdir(parents=True, exist_ok=True)
        self.output_input.setText(str(output_dir))
        self._refresh_queue_summary()

    def _populate_pick_target_menu(self) -> None:
        menu = self.pick_target_menu
        menu.clear()
        crosshair_action = menu.addAction("Pick with Crosshair...")
        crosshair_action.triggered.connect(self._pick_window_crosshair)
        menu.addSeparator()

        windows = self._capture_service.list_top_windows(
            int(self.winId()),
            include_minimized=True,
        )
        if not windows:
            empty_action = menu.addAction("No visible windows")
            empty_action.setEnabled(False)
            return
        for info in sorted(windows, key=lambda item: item.sort_key):
            display = f"{info.label} (minimized)" if info.is_minimized else info.label
            action = menu.addAction(display)
            action.setData(info.hwnd)
            action.triggered.connect(self._pick_window_from_menu_action)

    def _pick_window_from_menu_action(self) -> None:
        sender = self.sender()
        if not isinstance(sender, QAction):
            return
        hwnd_data = sender.data()
        if hwnd_data is None:
            return
        try:
            hwnd = int(hwnd_data)
        except (TypeError, ValueError):
            return
        self._set_target(self._picked_window_from_hwnd(hwnd))

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
        self._set_target(self._picked_window_from_hwnd(hwnd))

    def _pick_second_last_window(self) -> bool:
        hwnd = self._capture_service.resolve_second_last_window(int(self.winId()))
        if hwnd is None:
            self.status_label.setText("No second-last active window found.")
            return False
        self._set_target(self._picked_window_from_hwnd(hwnd))
        return True

    def _ensure_capture_target_selected(self, capture_kind: str) -> bool:
        if self._selected_target is not None:
            return True
        CAPTURE_UI_LOGGER.info(
            "%s requested without target; auto-selecting second-last active window",
            capture_kind,
        )
        if self._pick_second_last_window():
            return True
        CAPTURE_UI_LOGGER.error(
            "%s aborted: no selected target and no second-last active window",
            capture_kind,
        )
        self.status_label.setText("No target selected and no second-last active window found.")
        return False

    def _picked_window_from_hwnd(self, hwnd: int) -> PickedWindow:
        info = self._capture_service.window_info(hwnd)
        if info is not None:
            return self._picked_window_from_info(info)
        title = self._capture_service.window_title(hwnd).strip() or f"hwnd:{hwnd}"
        label = f"unknown - {title} [0, {hwnd}]"
        return PickedWindow(hwnd=hwnd, label=label, title=title)

    @staticmethod
    def _picked_window_from_info(info: WindowInfo) -> PickedWindow:
        return PickedWindow(hwnd=info.hwnd, label=info.label, title=info.title)

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
                item.title.lower(),
            ),
        )
        self._set_target(self._picked_window_from_info(best))
        self.status_label.setText(f"Default browser target selected: {best.label}")
        self._append_capture_log(
            f"Default browser target selected ({best.process_name or 'browser'})."
        )

    def _capture_last_selected_window(self) -> None:
        CAPTURE_UI_LOGGER.info("quick capture requested: capture_last_selected_window")
        hwnd = self._capture_service.resolve_alt_tab_target(
            int(self.winId()),
            retries=1,
        )
        if hwnd is None:
            CAPTURE_UI_LOGGER.error("quick capture failed: Alt+Tab did not resolve a valid target")
            self.status_label.setText("Alt+Tab did not resolve a valid target window.")
            self._append_capture_log("Alt+Tab quick capture failed: no valid foreground target.")
            return
        self._set_target(self._picked_window_from_hwnd(hwnd))
        self._append_capture_log(f"Alt+Tab selected target: {self._selected_target.label}.")
        self._capture_selected_viewport()

    def _set_target(self, target: PickedWindow) -> None:
        self._selected_target = target
        CAPTURE_UI_LOGGER.info("target selected hwnd=%s label=%r", target.hwnd, target.label)
        self.target_label.setText(f"Target: {target.label}")
        if self.base_input.text().strip() in {"", "capture"}:
            seed_name = target.title or target.label
            self.base_input.setText(sanitize_basename(seed_name))
        self.status_label.setText("Target selected.")

    def _capture_selected_viewport(self) -> None:
        CAPTURE_UI_LOGGER.info("viewport capture requested")
        if not self._ensure_capture_target_selected("viewport capture"):
            return
        CAPTURE_UI_LOGGER.info(
            "viewport capture target hwnd=%s label=%r backend=%s frame_region=%s include_cursor=%s",
            self._selected_target.hwnd,
            self._selected_target.label,
            self._capture_backend_primary(),
            self._capture_frame_region(),
            self._capture_include_mouse_cursor(),
        )
        focused, message = self._capture_service.activate_window(self._selected_target.hwnd)
        if not focused:
            CAPTURE_UI_LOGGER.error(
                "viewport capture focus failed hwnd=%s reason=%s",
                self._selected_target.hwnd,
                message or "unknown",
            )
            self.status_label.setText(message)
            return
        try:
            pixmap, backend_used = self._capture_service.capture_window(
                self._selected_target.hwnd,
                primary_backend=self._capture_backend_primary(),
                frame_region=self._capture_frame_region(),
                include_mouse_cursor=self._capture_include_mouse_cursor(),
            )
            if pixmap is None:
                CAPTURE_UI_LOGGER.error(
                    "viewport capture failed hwnd=%s backend=%s",
                    self._selected_target.hwnd,
                    backend_used or self._capture_backend_primary(),
                )
                self.status_label.setText("Capture failed.")
                return
            image = ImageQt.fromqpixmap(pixmap).convert("RGB")
            capture_title = self._selected_target.title or self._selected_target.label
            self._add_capture(
                image=image,
                title=capture_title,
                source_hwnd=self._selected_target.hwnd,
                frame_count=1,
            )
            CAPTURE_UI_LOGGER.info(
                "viewport capture complete hwnd=%s backend=%s",
                self._selected_target.hwnd,
                backend_used or "unknown",
            )
            self.status_label.setText(
                f"Captured selected viewport ({backend_used or 'unknown backend'})."
            )
        finally:
            self._restore_focus_to_app()

    def _capture_full_scroll(self) -> None:
        CAPTURE_UI_LOGGER.info("full capture requested")
        if not self._ensure_capture_target_selected("full capture"):
            return
        if self._capture_worker is not None and self._capture_worker.isRunning():
            CAPTURE_UI_LOGGER.warning("full capture ignored: worker already running")
            self.status_label.setText("Capture already running.")
            return
        CAPTURE_UI_LOGGER.info(
            "full capture target hwnd=%s label=%r backend=%s scroll_mode=%s wheel=%s cursor=%s frame_region=%s include_cursor=%s scroll_to_top=%s auto_trim=%s auto_trim_scrollbar=%s",
            self._selected_target.hwnd,
            self._selected_target.label,
            self._capture_backend_primary(),
            self._capture_scroll_mode(),
            self._capture_wheel_injection_mode(),
            self._capture_cursor_hold_mode(),
            self._capture_frame_region(),
            self._capture_include_mouse_cursor(),
            self._capture_scroll_to_top_on_full(),
            self._capture_auto_trim_fixed_strips(),
            True,
        )
        effective_wheel_mode = self._capture_wheel_injection_mode()
        if effective_wheel_mode == "legacy_message_wheel":
            effective_wheel_mode = DEFAULT_WHEEL_INJECTION_MODE
            CAPTURE_UI_LOGGER.warning(
                "legacy wheel mode overridden for full capture; using %s",
                effective_wheel_mode,
            )
            self._append_capture_log(
                "Legacy wheel mode overridden to Physical Center (SendInput) for full capture."
            )
        focused, message = self._capture_service.ensure_window_foreground(
            self._selected_target.hwnd
        )
        if not focused:
            CAPTURE_UI_LOGGER.error(
                "full capture preflight focus failed hwnd=%s reason=%s",
                self._selected_target.hwnd,
                message or "unknown",
            )
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
                scroll_mode=self._capture_scroll_mode(),
                wheel_injection_mode=effective_wheel_mode,
                cursor_hold_mode=self._capture_cursor_hold_mode(),
                frame_region=self._capture_frame_region(),
                include_mouse_cursor=self._capture_include_mouse_cursor(),
                scroll_to_top_on_full=self._capture_scroll_to_top_on_full(),
                auto_trim_fixed_strips=self._capture_auto_trim_fixed_strips(),
                auto_trim_scrollbar=True,
            ),
            stop_event=self._stop_event,
        )
        self._capture_worker.started.connect(self._on_full_capture_worker_started)
        self._capture_worker.capture_succeeded.connect(self._full_capture_done)
        self._capture_worker.capture_failed.connect(self._full_capture_failed)
        self._capture_worker.capture_progress.connect(self._on_full_capture_progress)
        self._capture_worker.finished.connect(self._full_capture_finished)
        self.capture_log_list.clear()
        self._append_capture_log(
            f"Target focused: {self._selected_target.label}."
        )
        self._append_capture_log(
            "Full capture started "
            f"(backend={self._capture_backend_primary()}, scroll_mode={self._capture_scroll_mode()}, "
            f"wheel={self._capture_wheel_injection_mode()}, "
            f"cursor={self._capture_cursor_hold_mode()}, "
            f"frame_region={self._capture_frame_region()}, "
            f"include_cursor={self._capture_include_mouse_cursor()}, "
            f"scroll_to_top={self._capture_scroll_to_top_on_full()}, "
            f"auto_trim={self._capture_auto_trim_fixed_strips()}, "
            "auto_trim_scrollbar=True)."
        )
        try:
            CAPTURE_UI_LOGGER.info("full capture show stop overlay")
            self._stop_overlay.show_top_right()
            CAPTURE_UI_LOGGER.info(
                "full capture worker start() call hwnd=%s",
                self._selected_target.hwnd,
            )
            self._capture_worker.start()
            running_now = self._capture_worker.isRunning()
            CAPTURE_UI_LOGGER.info(
                "full capture worker start returned isRunning=%s",
                running_now,
            )
            if not running_now:
                CAPTURE_UI_LOGGER.error(
                    "full capture worker did not start; falling back to inline run()"
                )
                self._append_capture_log(
                    "Worker thread did not start; falling back to inline capture run."
                )
                self._capture_worker.run()
                self._full_capture_finished()
                return
            QTimer.singleShot(400, self._probe_full_capture_worker_state)
        except Exception:  # pragma: no cover
            CAPTURE_UI_LOGGER.exception("full capture launch failed before worker run")
            self._append_capture_log("Full capture launch failed before worker start.")
            self.status_label.setText("Full capture launch failed.")
            self._stop_overlay.hide()
            self._capture_worker = None
            return
        self.status_label.setText("Full capture running. Hover red stop badge or press Ctrl+Shift+X.")

    def _request_stop(self) -> None:
        self._stop_event.set()
        CAPTURE_UI_LOGGER.info("stop requested by user")
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
        CAPTURE_UI_LOGGER.debug("progress %s", status)
        self._append_capture_log(progress_obj.message or status)

    def _full_capture_done(self, result_obj: object) -> None:
        image = getattr(result_obj, "image", None)
        if image is None:
            self.status_label.setText("Invalid full capture result.")
            return
        frame_count = int(getattr(result_obj, "captured_frames", 0))
        self._add_capture(
            image=image,
            title=(
                self._selected_target.title or self._selected_target.label
                if self._selected_target
                else "capture"
            ),
            source_hwnd=self._selected_target.hwnd if self._selected_target else None,
            frame_count=frame_count,
        )
        stop_reason = str(getattr(result_obj, "stop_reason", "max_pages"))
        summary = (
            f"Full capture complete: {frame_count} frame(s), "
            f"stop={self._describe_stop_reason(stop_reason)}."
        )
        CAPTURE_UI_LOGGER.info(
            "full capture done frame_count=%s stop_reason=%s",
            frame_count,
            stop_reason,
        )
        self.status_label.setText(summary)
        self._append_capture_log(summary)

    def _full_capture_failed(self, message: str) -> None:
        CAPTURE_UI_LOGGER.error("full capture failed message=%s", message)
        self.status_label.setText(f"Full capture failed: {message}")
        self._append_capture_log(f"Full capture failed: {message}")

    def _full_capture_finished(self) -> None:
        CAPTURE_UI_LOGGER.info("full capture finished cleanup")
        self._stop_overlay.hide()
        self._capture_worker = None
        self._restore_focus_to_app()

    def _on_full_capture_worker_started(self) -> None:
        CAPTURE_UI_LOGGER.info("full capture worker started signal received")
        self._append_capture_log("Full capture worker thread started.")

    def _probe_full_capture_worker_state(self) -> None:
        running = self._capture_worker is not None and self._capture_worker.isRunning()
        CAPTURE_UI_LOGGER.info("full capture worker probe running=%s", running)
        if not running:
            self._append_capture_log("Full capture worker is not running after start.")

    def _restore_focus_to_app(self) -> None:
        own_hwnd = int(self.winId())
        self.raise_()
        self.activateWindow()
        focused, message = self._capture_service.ensure_window_foreground(own_hwnd)
        if focused:
            CAPTURE_UI_LOGGER.info("focus restored to app hwnd=%s", own_hwnd)
            self._append_capture_log("Focus returned to web-pagez-to-pdf.")
            return
        CAPTURE_UI_LOGGER.error(
            "focus restore failed hwnd=%s reason=%s",
            own_hwnd,
            message or "unknown",
        )
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
        self._editor_scroll_to_top_pending = True
        self._refresh_queue_summary()

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
        self._effective_auto_split_markers = []
        if self._split_action_mode != "none":
            self._set_split_action_mode(
                "none",
                reason="Split marker action canceled because queue selection changed.",
            )
        self._set_editor_zoom_mode("fit_width")
        self._sync_editor_controls()
        self._sync_split_marker_list()
        self._refresh_preview()
        self._refresh_queue_summary()

    def _on_tab_changed(self, index: int) -> None:
        if index != 1 and self._split_action_mode != "none":
            self._set_split_action_mode("none")
        if index == 0 and len(self._int_list_setting("ui.capture_splitter_sizes")) < 2:
            QTimer.singleShot(0, self._apply_capture_splitter_default)
        if index == 1 and len(self._int_list_setting("ui.editor_splitter_sizes")) < 2:
            QTimer.singleShot(0, self._apply_editor_splitter_default)
        if index == 1 and self._editor_scroll_to_top_pending:
            self._editor_scroll_to_top_pending = False
            QTimer.singleShot(0, self.editor_canvas.scroll_to_top)

    def _session_for_item(self, item_id: str) -> EditAdjustments:
        existing = self._sessions.edits_by_item_id.get(item_id)
        if existing is None:
            existing = EditAdjustments()
            self._sessions.edits_by_item_id[item_id] = existing
        return existing

    def _current_session(self) -> EditAdjustments | None:
        current_item = self._current_item()
        if current_item is None:
            return None
        return self._session_for_item(current_item.item_id)

    def _history_item_row(self, item_id: str | None) -> int:
        if not item_id:
            return -1
        for index, item in enumerate(self._queue):
            if item.item_id == item_id:
                return index
        return -1

    def _snapshot_history_entry(self) -> _EditorHistoryEntry:
        current = self._current_item()
        return _EditorHistoryEntry(
            edits_by_item_id={
                item_id: edits.clone() for item_id, edits in self._sessions.edits_by_item_id.items()
            },
            selected_item_id=current.item_id if current is not None else None,
        )

    def _push_history_if_changed(self, before: _EditorHistoryEntry) -> bool:
        if self._history_restoring:
            return False
        after = self._snapshot_history_entry()
        if before.edits_by_item_id == after.edits_by_item_id:
            return False
        self._undo_history.append(before)
        if len(self._undo_history) > self._history_limit:
            self._undo_history = self._undo_history[-self._history_limit :]
        self._redo_history.clear()
        self._update_history_actions()
        return True

    def _restore_history_entry(self, entry: _EditorHistoryEntry) -> None:
        self._history_restoring = True
        try:
            self._sessions.edits_by_item_id = {
                item_id: edits.clone() for item_id, edits in entry.edits_by_item_id.items()
            }
            row = self._history_item_row(entry.selected_item_id)
            with QSignalBlocker(self.queue_list):
                if row >= 0:
                    self.queue_list.setCurrentRow(row)
            self._sync_editor_controls()
            self._sync_split_marker_list()
            self._refresh_preview()
            self._refresh_queue_summary()
        finally:
            self._history_restoring = False
        self._update_history_actions()

    def _update_history_actions(self) -> None:
        can_undo = bool(self._undo_history)
        can_redo = bool(self._redo_history)
        if self._undo_action is not None:
            self._undo_action.setEnabled(can_undo)
        if self._redo_action is not None:
            self._redo_action.setEnabled(can_redo)

    def _undo_editor_change(self) -> None:
        if not self._undo_history:
            self.status_label.setText("Nothing to undo.")
            self._update_history_actions()
            return
        current = self._snapshot_history_entry()
        entry = self._undo_history.pop()
        self._redo_history.append(current)
        self._restore_history_entry(entry)
        self.status_label.setText("Undo applied.")

    def _redo_editor_change(self) -> None:
        if not self._redo_history:
            self.status_label.setText("Nothing to redo.")
            self._update_history_actions()
            return
        current = self._snapshot_history_entry()
        entry = self._redo_history.pop()
        self._undo_history.append(current)
        self._restore_history_entry(entry)
        self.status_label.setText("Redo applied.")

    def _editor_controls_changed(self, *_args: object) -> None:
        if self._editor_loading:
            return
        current_item = self._current_item()
        if current_item is None:
            return
        selected_mode = normalize_content_sizing_mode(self.content_sizing_mode_combo.currentData())
        self._pending_transform_item_id = current_item.item_id
        self._pending_transform_values = (
            int(self.zoom_spin.value()),
            int(self.rotate_spin.value()),
            int(self.straighten_spin.value()),
            selected_mode,
        )
        self._schedule_debounced_preview_update(transform_changed=True)

    def _schedule_debounced_preview_update(
        self,
        *,
        transform_changed: bool = False,
        layout_changed: bool = False,
    ) -> None:
        self._preview_update_pending_transform = (
            self._preview_update_pending_transform or bool(transform_changed)
        )
        self._preview_update_pending_layout = (
            self._preview_update_pending_layout or bool(layout_changed)
        )
        if not self._preview_update_pending_transform and not self._preview_update_pending_layout:
            return
        if self._editor_preview_debounce_ms <= 0:
            self._flush_debounced_preview_update()
            return
        self._preview_update_timer.start(self._editor_preview_debounce_ms)

    def _flush_debounced_preview_update(self) -> None:
        self._preview_update_timer.stop()
        pending_transform = self._preview_update_pending_transform
        pending_layout = self._preview_update_pending_layout
        self._preview_update_pending_transform = False
        self._preview_update_pending_layout = False
        if not pending_transform and not pending_layout:
            return

        if pending_transform:
            item_id = self._pending_transform_item_id
            transform_values = self._pending_transform_values
            self._pending_transform_item_id = None
            self._pending_transform_values = None
            if item_id and transform_values:
                edits = self._sessions.edits_by_item_id.get(item_id)
            else:
                edits = None
            if edits is not None and transform_values is not None:
                zoom_percent, rotate_degrees, straighten_degrees, content_sizing_mode = transform_values
                before = self._snapshot_history_entry()
                self._set_scalar_operation(
                    edits,
                    "scale",
                    "percent",
                    int(zoom_percent),
                    100,
                )
                self._set_scalar_operation(
                    edits,
                    "rotate",
                    "degrees",
                    int(rotate_degrees),
                    0,
                )
                self._set_scalar_operation(
                    edits,
                    "straighten",
                    "degrees",
                    int(straighten_degrees),
                    0,
                )
                self._set_content_sizing_mode_operation(edits, content_sizing_mode)
                self._push_history_if_changed(before)

        self._refresh_preview()

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

    def _content_sizing_mode_for_edits(self, edits: EditAdjustments | None) -> str:
        if edits is None:
            return DEFAULT_CONTENT_SIZING_MODE
        mode_op = edits.get_operation("content_sizing_mode")
        mode_value = mode_op.params.get("mode") if mode_op is not None else DEFAULT_CONTENT_SIZING_MODE
        return normalize_content_sizing_mode(mode_value)

    def _set_content_sizing_mode_operation(self, edits: EditAdjustments, mode: str) -> None:
        normalized = normalize_content_sizing_mode(mode)
        if normalized == DEFAULT_CONTENT_SIZING_MODE:
            edits.remove_operation("content_sizing_mode")
            return
        edits.set_operation("content_sizing_mode", {"mode": normalized})

    def _sync_editor_controls(self) -> None:
        edits = self._current_session()
        self._editor_loading = True
        try:
            if edits is None:
                self.content_sizing_mode_combo.setCurrentIndex(0)
                self.zoom_spin.setValue(100.0)
                self.rotate_spin.setValue(0)
                self.straighten_spin.setValue(0)
                return
            scale_op = edits.get_operation("scale")
            rotate_op = edits.get_operation("rotate")
            straighten_op = edits.get_operation("straighten")
            sizing_mode = self._content_sizing_mode_for_edits(edits)
            mode_index = self.content_sizing_mode_combo.findData(sizing_mode)
            self.content_sizing_mode_combo.setCurrentIndex(max(0, mode_index))
            self.zoom_spin.setValue(float(scale_op.params.get("percent", 100)) if scale_op else 100.0)
            self.rotate_spin.setValue(int(rotate_op.params.get("degrees", 0)) if rotate_op else 0)
            self.straighten_spin.setValue(
                int(straighten_op.params.get("degrees", 0)) if straighten_op else 0
            )
        finally:
            self._editor_loading = False

    @staticmethod
    def _normalized_markers(values: list[int]) -> list[int]:
        return sorted({int(value) for value in values if int(value) > 0})

    @staticmethod
    def _auto_split_markers_from_slices(
        slices: list[tuple[int, int]],
        *,
        image_height: int,
    ) -> list[int]:
        return sorted(
            {
                int(bottom)
                for _top, bottom in slices
                if 0 < int(bottom) < int(image_height)
            }
        )

    def _effective_split_markers(self) -> list[int]:
        persisted = self._split_markers()
        if persisted:
            return persisted
        return self._normalized_markers(list(self._effective_auto_split_markers))

    def _editable_split_markers_base(self) -> list[int]:
        persisted = self._split_markers()
        if persisted:
            return list(persisted)
        return self._effective_split_markers()

    def _nearest_effective_split_marker(
        self, marker_y: int, *, tolerance_px: int
    ) -> int | None:
        markers = self._effective_split_markers()
        if not markers:
            return None
        requested = int(marker_y)
        nearest = min(markers, key=lambda marker: abs(int(marker) - requested))
        if abs(int(nearest) - requested) > int(max(1, tolerance_px)):
            return None
        return int(nearest)

    def _sync_split_marker_list(self, *, selected_marker: int | None = None) -> None:
        current_item = self._current_item()
        selected_value = selected_marker
        if selected_value is None and self.split_list.currentRow() >= 0:
            selected_value = self._split_marker_at_row(self.split_list.currentRow())
        markers = self._effective_split_markers()
        with QSignalBlocker(self.split_list):
            self.split_list.clear()
            if current_item is None:
                return
            for marker in markers:
                marker_item = QListWidgetItem(str(marker))
                self.split_list.addItem(marker_item)
            if selected_value is not None:
                for row in range(self.split_list.count()):
                    marker = self._split_marker_at_row(row)
                    if marker == selected_value:
                        self.split_list.setCurrentRow(row)
                        break
        if self.split_list.currentRow() < 0 and self.split_list.count() > 0:
            self.split_list.setCurrentRow(0)

    def _split_marker_at_row(self, row: int) -> int | None:
        if row < 0 or row >= self.split_list.count():
            return None
        item = self.split_list.item(row)
        if item is None:
            return None
        try:
            marker = int(item.text().strip())
        except (TypeError, ValueError):
            return None
        if marker <= 0:
            return None
        return marker

    def _set_manual_split_markers(
        self,
        markers: list[int],
        *,
        selected_marker: int | None = None,
    ) -> None:
        self._flush_debounced_preview_update()
        edits = self._current_session()
        if edits is None:
            return
        before = self._snapshot_history_entry()
        normalized = self._normalized_markers(markers)
        edits.split_markers_px = normalized
        self._push_history_if_changed(before)
        self._sync_split_marker_list(selected_marker=selected_marker)
        self._refresh_preview()

    def _reset_item_edits(self) -> None:
        self._flush_debounced_preview_update()
        item = self._current_item()
        if item is None:
            return
        before = self._snapshot_history_entry()
        self._sessions.edits_by_item_id[item.item_id] = EditAdjustments()
        self._push_history_if_changed(before)
        self._sync_editor_controls()
        self._sync_split_marker_list()
        self._refresh_preview()

    def _refresh_queue_summary(self) -> None:
        selected = self.queue_list.currentRow() + 1 if self.queue_list.currentRow() >= 0 else 0
        self.queue_summary_label.setText(f"Queue: {len(self._queue)} item(s), selected: {selected}")

    def _append_capture_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        CAPTURE_UI_LOGGER.info("ui-log %s", text)
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

    @staticmethod
    def _rich_text_has_meaningful_content(rich_html: str) -> bool:
        source = str(rich_html or "")
        if not source.strip():
            return False
        doc = QTextDocument()
        doc.setHtml(source)
        plain_text = doc.toPlainText().replace("\u200b", "").replace("\ufeff", "")
        if re.sub(r"\s+", "", plain_text):
            return True
        return bool(
            re.search(
                r"<\s*(img|svg|canvas|object|iframe|video|audio|hr)\b",
                source,
                flags=re.IGNORECASE,
            )
        )

    def _effective_rich_html(self, rich_html: str) -> str:
        return rich_html if self._rich_text_has_meaningful_content(rich_html) else ""

    @staticmethod
    def _apply_layout_tokens(text: str, context: dict[str, str]) -> str:
        result = str(text or "")
        for key, value in context.items():
            result = result.replace(f"{{{key}}}", value)
        return result

    def _render_preview_rich_text(
        self,
        qimage,
        *,
        rich_html: str,
        context: dict[str, str],
        left: int,
        top: int,
        width: int,
        height: int,
        align_bottom: bool = False,
    ) -> None:
        if width <= 4 or height <= 4:
            return
        effective_html = self._effective_rich_html(rich_html)
        rendered_html = self._apply_layout_tokens(effective_html, context).strip()
        if not rendered_html:
            return

        doc = QTextDocument()
        doc.setDocumentMargin(0.0)
        doc.setHtml(rendered_html)
        doc.setTextWidth(float(max(1, width - 6)))
        doc_height = float(doc.size().height())
        if doc_height <= 0.0:
            return

        text_top = float(top + 3)
        if align_bottom:
            text_top = float(top + max(1, height - int(round(doc_height)) - 3))

        painter = QPainter(qimage)
        try:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setPen(QColor(24, 24, 24))
            painter.save()
            painter.setClipRect(int(left), int(top), int(width), int(height))
            painter.translate(float(left + 3), text_top)
            doc.drawContents(painter)
            painter.restore()
        finally:
            painter.end()

    def _collect_layout(self) -> PrintLayout:
        header_html = self._effective_rich_html(self.header_input.toHtml())
        footer_html = self._effective_rich_html(self.footer_input.toHtml())
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
            header_rich_text=header_html,
            footer_rich_text=footer_html,
        )

    def _on_layout_controls_changed(self, *_args: object) -> None:
        if self._editor_loading:
            return
        self._schedule_debounced_preview_update(layout_changed=True)

    def _on_overlay_visibility_changed(self, checked: bool) -> None:
        self.editor_canvas.set_overlay_visibility(bool(checked))
        if self._editor_loading:
            return
        self._refresh_preview()

    def _on_thumbnail_zoom_slider_changed(self, value: int) -> None:
        self._apply_thumbnail_icon_size(int(value))

    def _on_thumbnail_zoom_delta_requested(self, delta: int) -> None:
        next_value = int(self.thumbnail_zoom_slider.value()) + int(delta)
        next_value = max(self.thumbnail_zoom_slider.minimum(), min(next_value, self.thumbnail_zoom_slider.maximum()))
        self.thumbnail_zoom_slider.setValue(next_value)

    def _apply_thumbnail_icon_size(self, value: int) -> None:
        width = max(90, min(320, int(value)))
        height = round(width * 1.4)
        target_size = QSize(width, height)
        self.page_preview_list.setIconSize(target_size)
        pixmap_role = int(Qt.ItemDataRole.UserRole) + 1
        for row in range(self.page_preview_list.count()):
            item = self.page_preview_list.item(row)
            if item is None:
                continue
            source_obj = item.data(pixmap_role)
            if not isinstance(source_obj, QPixmap) or source_obj.isNull():
                continue
            item.setIcon(
                QIcon(
                    source_obj.scaled(
                        target_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            )

    def _clear_page_preview_hover_overlay(self) -> None:
        self.editor_canvas.set_hover_overlay_pixmap(None)

    def _on_page_preview_item_hovered(self, item: QListWidgetItem) -> None:
        if item is None:
            self._clear_page_preview_hover_overlay()
            return
        row = self.page_preview_list.row(item)
        overlay = self._preview_page_hover_overlays.get(row)
        if overlay is None or overlay.isNull():
            self._clear_page_preview_hover_overlay()
            return
        self.editor_canvas.set_hover_overlay_pixmap(overlay)

    def _on_page_preview_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._current_preview_slices):
            return
        top, bottom = self._current_preview_slices[row]
        self.editor_canvas.focus_on_y((float(top) + float(bottom)) / 2.0)

    def _decorate_page_thumbnail(
        self,
        page_image: Image.Image,
        *,
        layout: PrintLayout | None = None,
        page_title: str = "",
        page_index: int = 1,
        page_count: int = 1,
        content_points_per_pixel: float | None = None,
    ) -> Image.Image:
        active_layout = layout if layout is not None else self._collect_layout()
        page_name = str(active_layout.paper_name or "A4").strip().upper()
        page_w_pt, page_h_pt = PAPER_SIZES.get(page_name, PAPER_SIZES["A4"])
        if str(active_layout.orientation or "portrait").strip().lower() == "landscape":
            page_w_pt, page_h_pt = page_h_pt, page_w_pt

        slice_w = max(1, int(page_image.width))
        slice_h = max(1, int(page_image.height))
        margin_left_pt = max(0.0, float(active_layout.margin_left_mm) * mm)
        margin_right_pt = max(0.0, float(active_layout.margin_right_mm) * mm)
        margin_top_pt = max(0.0, float(active_layout.margin_top_mm) * mm)
        margin_bottom_pt = max(0.0, float(active_layout.margin_bottom_mm) * mm)
        gutter_pt = max(0.0, float(active_layout.gutter_mm) * mm)
        if content_points_per_pixel is None:
            avail_w_pt = float(page_w_pt) - (margin_left_pt + margin_right_pt + gutter_pt)
            if avail_w_pt <= 1.0:
                avail_w_pt = 1.0
            points_per_px = max(0.0001, float(avail_w_pt) / float(slice_w))
        else:
            points_per_px = max(0.0001, float(content_points_per_pixel))
        px_per_pt = 1.0 / points_per_px

        page_w_px = max(slice_w + 2, int(round(float(page_w_pt) * px_per_pt)))
        page_h_px = max(slice_h + 2, int(round(float(page_h_pt) * px_per_pt)))
        decorated = Image.new("RGB", (page_w_px, page_h_px), (244, 244, 244))
        draw = ImageDraw.Draw(decorated, "RGBA")
        legend_h = 22

        left_margin_boundary = max(0, min(page_w_px - 2, int(round(margin_left_pt * px_per_pt))))
        printable_left = max(
            0,
            min(page_w_px - 2, int(round((margin_left_pt + gutter_pt) * px_per_pt))),
        )
        printable_top = max(0, min(page_h_px - 2, int(round(margin_top_pt * px_per_pt))))
        printable_right = max(
            printable_left + 1,
            min(
                page_w_px - 1,
                int(round((float(page_w_pt) - margin_right_pt) * px_per_pt)),
            ),
        )
        printable_bottom = max(
            printable_top + 1,
            min(
                page_h_px - 1,
                int(round((float(page_h_pt) - margin_bottom_pt) * px_per_pt)),
            ),
        )

        image_left = int(printable_left)
        image_top = int(printable_top)
        max_render_w = max(1, page_w_px - image_left - 1)
        image_width = max(1, min(int(slice_w), int(max_render_w)))
        visible_height = max(1, min(int(slice_h), int(printable_bottom - image_top)))
        image_right = max(image_left + 1, min(page_w_px - 1, image_left + image_width))
        image_crop = page_image.crop((0, 0, image_width, visible_height)).convert("RGB")
        decorated.paste(image_crop, (image_left, image_top))

        outer = (0, 0, max(0, page_w_px - 1), max(0, page_h_px - 1))
        draw.rectangle(outer, outline=THUMBNAIL_BORDER_CUE_COLOR, width=2)
        draw.rectangle(
            (
                printable_left,
                printable_top,
                max(printable_left + 1, printable_right),
                max(printable_top + 1, printable_bottom),
            ),
            outline=THUMBNAIL_MARGIN_CUE_COLOR,
            width=3,
        )
        if printable_left > left_margin_boundary:
            draw.rectangle(
                (
                    left_margin_boundary,
                    printable_top,
                    printable_left,
                    printable_bottom,
                ),
                fill=(
                    int(THUMBNAIL_GUTTER_CUE_COLOR[0]),
                    int(THUMBNAIL_GUTTER_CUE_COLOR[1]),
                    int(THUMBNAIL_GUTTER_CUE_COLOR[2]),
                    82,
                ),
                outline=THUMBNAIL_GUTTER_CUE_COLOR,
                width=2,
            )

        preview_context = {
            "title": str(page_title or "preview"),
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "page": str(max(1, int(page_index))),
            "pages": str(max(1, int(page_count))),
        }
        qimage = ImageQt.ImageQt(decorated)
        header_top = max(0, legend_h + 4)
        header_height = max(0, printable_top - header_top)
        footer_top = max(0, printable_bottom + 1)
        footer_height = max(0, page_h_px - footer_top - 1)
        self._render_preview_rich_text(
            qimage,
            rich_html=active_layout.header_rich_text,
            context=preview_context,
            left=printable_left,
            top=header_top,
            width=max(1, printable_right - printable_left),
            height=header_height,
            align_bottom=False,
        )
        self._render_preview_rich_text(
            qimage,
            rich_html=active_layout.footer_rich_text,
            context=preview_context,
            left=printable_left,
            top=footer_top,
            width=max(1, printable_right - printable_left),
            height=footer_height,
            align_bottom=True,
        )
        decorated = ImageQt.fromqimage(qimage).convert("RGB")
        draw = ImageDraw.Draw(decorated, "RGBA")

        legend_w = min(page_w_px - 4, max(96, round(page_w_px * 0.86)))
        draw.rectangle((2, 2, legend_w, legend_h), fill=(0, 0, 0, 180))
        legend_segments = (
            ("Margins", THUMBNAIL_MARGIN_CUE_COLOR),
            (" | ", (198, 198, 198, 236)),
            ("Gutter", THUMBNAIL_GUTTER_CUE_COLOR),
            (" | ", (198, 198, 198, 236)),
            ("Border", THUMBNAIL_BORDER_CUE_COLOR),
        )
        cursor_x = 8.0
        for text, color in legend_segments:
            draw.text((cursor_x, 6), text, fill=color)
            cursor_x += float(draw.textlength(text))
        return decorated

    def _sync_page_preview_list(
        self,
        preview: Image.Image,
        slices: list[tuple[int, int]],
        layout: PrintLayout,
        *,
        item_title: str,
        content_points_per_pixel: float,
    ) -> None:
        self._clear_page_preview_hover_overlay()
        pixmap_role = int(Qt.ItemDataRole.UserRole) + 1
        selected_row = self.page_preview_list.currentRow()
        self._preview_page_hover_overlays = {}
        with QSignalBlocker(self.page_preview_list):
            self.page_preview_list.clear()
            page_count = max(1, len(slices))
            for index, (top, bottom) in enumerate(slices, start=1):
                cropped = preview.crop((0, top, preview.width, bottom))
                decorated = self._decorate_page_thumbnail(
                    cropped,
                    layout=layout,
                    page_title=item_title,
                    page_index=index,
                    page_count=page_count,
                    content_points_per_pixel=content_points_per_pixel,
                )
                base_pixmap = pil_to_qpixmap(decorated)
                thumbnail = base_pixmap.scaled(
                    self.page_preview_list.iconSize(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                item = QListWidgetItem(f"Page {index} ({max(1, bottom - top)} px)")
                item.setIcon(QIcon(thumbnail))
                item.setData(Qt.ItemDataRole.UserRole, (top, bottom))
                item.setData(pixmap_role, base_pixmap)
                self.page_preview_list.addItem(item)
                self._preview_page_hover_overlays[index - 1] = base_pixmap.scaled(
                    720,
                    1020,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            if self.page_preview_list.count() > 0:
                if selected_row < 0:
                    self.page_preview_list.setCurrentRow(0)
                else:
                    self.page_preview_list.setCurrentRow(
                        min(selected_row, self.page_preview_list.count() - 1)
                    )
        self._apply_thumbnail_icon_size(self.thumbnail_zoom_slider.value())

    def _set_editor_zoom_mode(
        self,
        mode: str,
        manual_percent: int | None = None,
        *,
        refresh_preview: bool = True,
    ) -> None:
        self.editor_canvas.set_zoom_mode(mode, manual_percent=manual_percent)
        self._sync_editor_view_zoom_controls()
        if refresh_preview:
            self._refresh_preview()

    def _adjust_editor_zoom(self, delta_percent: int) -> None:
        self.editor_canvas.adjust_manual_zoom(delta_percent)

    def _sync_editor_view_zoom_controls(self) -> None:
        self.zoom_status_label.setText(self.editor_canvas.zoom_label_text())
        with QSignalBlocker(self.editor_view_zoom_spin):
            self.editor_view_zoom_spin.setValue(self.editor_canvas.manual_zoom_percent())
        self.editor_view_zoom_spin.setEnabled(self.editor_canvas.zoom_mode() == "manual")

    def _on_editor_canvas_zoom_changed(self, _mode: str, _manual_percent: int) -> None:
        self._sync_editor_view_zoom_controls()

    def _on_editor_view_zoom_spin_changed(self, value: int) -> None:
        if self.editor_canvas.zoom_mode() == "manual" and self.editor_canvas.manual_zoom_percent() == int(
            value
        ):
            return
        self._set_editor_zoom_mode("manual", int(value), refresh_preview=False)

    @staticmethod
    def _tool_uses_crosshair_magnifier(tool: str) -> bool:
        normalized = str(tool or "").strip().lower()
        return normalized in {"crop_rect", "crop_vertical_band", "crop_free"}

    def _current_editor_tool(self) -> str:
        checked = self.editor_tool_buttons.checkedButton()
        if checked is None:
            return "pan"
        return str(checked.property("tool") or "pan")

    def _apply_magnifier_visibility_for_tool(self, tool: str | None = None) -> None:
        active_tool = str(tool if tool is not None else self._current_editor_tool())
        self.editor_canvas.set_magnifier_state(
            enabled=self._tool_uses_crosshair_magnifier(active_tool),
            zoom_factor=DEFAULT_CROSSHAIR_MAGNIFIER,
        )

    def _on_editor_tool_changed(self, button: QToolButton) -> None:
        tool = str(button.property("tool") or "pan")
        if tool != "pan" and self._split_action_mode != "none":
            self._set_split_action_mode(
                "none",
                reason="Split marker action canceled because another tool was selected.",
            )
        self.editor_canvas.set_tool(tool)
        self._apply_magnifier_visibility_for_tool(tool)
        if tool == "pan":
            self.status_label.setText("Pan active: drag to scroll the preview image.")

    def _activate_pan_mode(self, *, preserve_status: bool = False) -> None:
        if not self.pan_tool_button.isChecked():
            self.pan_tool_button.setChecked(True)
        self.editor_canvas.set_tool("pan")
        self._apply_magnifier_visibility_for_tool("pan")
        if not preserve_status:
            self.status_label.setText("Pan active: drag to scroll the preview image.")

    def _set_split_action_mode(self, mode: str, *, reason: str | None = None) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in {"none", "add", "remove"}:
            normalized = "none"
        self._split_action_mode = normalized
        self.editor_canvas.set_split_action_mode(normalized)
        if normalized == "add":
            self.status_label.setText(
                "Add Split Marker armed. Click the image area or Y ruler to place a split marker."
            )
            return
        if normalized == "remove":
            self.status_label.setText(
                "Remove Split Marker armed. Click a split marker on the image or Y ruler to remove it."
            )
            return
        if reason:
            self.status_label.setText(reason)

    def _clear_crop_operations(self, edits: EditAdjustments, *, keep: str | None = None) -> None:
        keep_normalized = str(keep or "").strip().lower()
        for op_type in (
            "crop_rect",
            "crop_free",
            "crop_vertical_band",
            "nav_auto_crop",
            "auto_vertical_border_crop",
        ):
            if op_type != keep_normalized:
                edits.remove_operation(op_type)

    def _on_editor_rect_drawn(self, tool: str, rect_obj: object) -> None:
        self._flush_debounced_preview_update()
        item = self._current_item()
        if item is None:
            self.status_label.setText("Select queue item first.")
            return
        if not isinstance(rect_obj, QRectF):
            return
        rect = rect_obj.normalized()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        payload = {
            "left": int(max(0.0, rect.left())),
            "top": int(max(0.0, rect.top())),
            "width": int(max(1.0, rect.width())),
            "height": int(max(1.0, rect.height())),
        }
        edits = self._session_for_item(item.item_id)
        before = self._snapshot_history_entry()
        normalized_tool = str(tool or "").strip().lower()
        if normalized_tool in {"crop_rect", "crop_vertical_band"}:
            self._clear_crop_operations(edits, keep=normalized_tool)
            edits.set_operation(normalized_tool, payload)
        elif normalized_tool == "redact":
            redaction = {
                "x": payload["left"],
                "y": payload["top"],
                "width": payload["width"],
                "height": payload["height"],
            }
            op = edits.get_operation("redact_rects")
            rectangles: list[dict[str, int]] = []
            if op is not None:
                raw = op.params.get("rectangles")
                if isinstance(raw, list):
                    for row in raw:
                        if isinstance(row, dict):
                            rectangles.append(deepcopy(row))
            rectangles.append(redaction)
            edits.set_operation("redact_rects", {"rectangles": rectangles})
        else:
            return
        self._push_history_if_changed(before)
        self._refresh_preview()
        self._activate_pan_mode()

    def _on_editor_free_crop(self, points_obj: object) -> None:
        self._flush_debounced_preview_update()
        item = self._current_item()
        if item is None:
            self.status_label.setText("Select queue item first.")
            return
        points = points_obj if isinstance(points_obj, list) else []
        normalized: list[list[int]] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            normalized.append([int(point[0]), int(point[1])])
        if len(normalized) < 3:
            return
        edits = self._session_for_item(item.item_id)
        before = self._snapshot_history_entry()
        self._clear_crop_operations(edits, keep="crop_free")
        edits.set_operation("crop_free", {"points": normalized})
        self._push_history_if_changed(before)
        self._refresh_preview()
        self._activate_pan_mode()

    def _refresh_preview(self, *_args: object) -> None:
        item = self._current_item()
        if item is None:
            self.editor_item_label.setText("No queue item selected.")
            self.editor_canvas.set_image(QPixmap())
            self.editor_canvas.set_page_overlays([], [], printable_width_px=0)
            self.editor_canvas.set_hover_overlay_pixmap(None)
            self._current_preview_slices = []
            self._effective_auto_split_markers = []
            self._current_content_points_per_pixel = 1.0
            self._preview_page_hover_overlays = {}
            with QSignalBlocker(self.page_preview_list):
                self.page_preview_list.clear()
            self.capture_tab_preview_label.setPixmap(QPixmap())
            self.capture_tab_preview_label.setText("No capture selected")
            self._sync_split_marker_list()
            return
        self.editor_item_label.setText(f"Editing: {item.title} [{item.image_path.name}]")
        if not item.image_path.exists():
            self.editor_item_label.setText(f"Editing: {item.title} [image missing]")
            self.editor_canvas.set_image(QPixmap())
            self.editor_canvas.set_page_overlays([], [], printable_width_px=0)
            self.editor_canvas.set_hover_overlay_pixmap(None)
            self._current_preview_slices = []
            self._effective_auto_split_markers = []
            self._current_content_points_per_pixel = 1.0
            self._preview_page_hover_overlays = {}
            with QSignalBlocker(self.page_preview_list):
                self.page_preview_list.clear()
            self.capture_tab_preview_label.setPixmap(QPixmap())
            self.capture_tab_preview_label.setText("Capture file missing")
            self._sync_split_marker_list()
            return
        image = Image.open(item.image_path).convert("RGB")
        edits = self._session_for_item(item.item_id)
        layout = self._collect_layout()
        preview = apply_edit_transform(
            image,
            layout,
            edits,
        )
        content_sizing_mode = self._content_sizing_mode_for_edits(edits)
        self._current_content_points_per_pixel = content_points_per_pixel(
            preview.width,
            preview.height,
            layout,
            content_sizing_mode,
        )
        slices = compute_page_slices(
            preview,
            layout,
            self._split_markers(),
            content_sizing_mode=content_sizing_mode,
        )
        self._current_preview_slices = [(slice_obj.top, slice_obj.bottom) for slice_obj in slices]
        self._effective_auto_split_markers = self._auto_split_markers_from_slices(
            self._current_preview_slices,
            image_height=preview.height,
        )
        effective_markers = self._effective_split_markers()
        full_pixmap = pil_to_qpixmap(preview)
        self.editor_canvas.set_image(full_pixmap)
        self.editor_canvas.set_hover_overlay_pixmap(None)
        self.editor_canvas.set_overlay_visibility(self.editor_overlay_toggle.isChecked())
        self._apply_magnifier_visibility_for_tool()
        self.editor_canvas.set_page_overlays(
            effective_markers,
            self._current_preview_slices,
            printable_width_px=preview.width,
        )
        self._sync_split_marker_list()
        self._sync_page_preview_list(
            preview,
            self._current_preview_slices,
            layout,
            item_title=item.title,
            content_points_per_pixel=self._current_content_points_per_pixel,
        )
        self._sync_editor_view_zoom_controls()
        thumb = full_pixmap.scaled(
            max(1, self.capture_tab_preview_label.width() - 8),
            max(1, self.capture_tab_preview_label.height() - 8),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.capture_tab_preview_label.setPixmap(thumb)
        self.capture_tab_preview_label.setText("")

    def _run_vertical_border_crop(self, *_args: object) -> None:
        self._flush_debounced_preview_update()
        self._apply_auto_vertical_border_crop_item()
        self._activate_pan_mode(preserve_status=True)

    def _apply_auto_vertical_border_crop_item(self) -> None:
        item = self._current_item()
        if item is None:
            self.status_label.setText("Select queue item first.")
            return
        image = Image.open(item.image_path).convert("RGB")
        left_px, right_px, left_ok, right_ok = suggest_auto_vertical_border_crop_with_confidence(
            image
        )
        before = self._snapshot_history_entry()
        edits = self._session_for_item(item.item_id)
        if (left_ok and left_px > 0) or (right_ok and right_px > 0):
            edits.remove_operation("nav_auto_crop")
            edits.set_operation(
                "auto_vertical_border_crop",
                {"left": int(left_px), "right": int(right_px)},
            )
            self.status_label.setText(
                "Auto vertical border crop applied "
                f"(left={int(left_px)}px right={int(right_px)}px)."
            )
        else:
            edits.remove_operation("auto_vertical_border_crop")
            edits.remove_operation("nav_auto_crop")
            self.status_label.setText(
                "Auto vertical border crop has insufficient confidence."
            )
        self._push_history_if_changed(before)
        self._refresh_preview()

    def _clear_redactions(self) -> None:
        self._flush_debounced_preview_update()
        edits = self._current_session()
        if edits is None:
            self.status_label.setText("Select queue item first.")
            return
        before = self._snapshot_history_entry()
        edits.remove_operation("redact_rects")
        self._push_history_if_changed(before)
        self._refresh_preview()
        self.status_label.setText("Redactions cleared.")

    def _arm_add_split_marker(self) -> None:
        if self._current_session() is None:
            self.status_label.setText("Select queue item first.")
            return
        self._activate_pan_mode(preserve_status=True)
        if self._split_action_mode == "add":
            self._set_split_action_mode("none", reason="Add Split Marker canceled.")
            return
        self._set_split_action_mode("add")

    def _arm_remove_split_marker(self) -> None:
        if self._current_session() is None:
            self.status_label.setText("Select queue item first.")
            return
        if not self._effective_split_markers():
            self.status_label.setText("No split markers are available to remove.")
            return
        self._activate_pan_mode(preserve_status=True)
        if self._split_action_mode == "remove":
            self._set_split_action_mode("none", reason="Remove Split Marker canceled.")
            return
        self._set_split_action_mode("remove")

    def _add_split_marker(self) -> None:
        self._arm_add_split_marker()

    def _remove_split_marker(self) -> None:
        self._arm_remove_split_marker()

    def _reset_split_markers(self) -> None:
        self._flush_debounced_preview_update()
        edits = self._current_session()
        if edits is None:
            self.status_label.setText("Select queue item first.")
            return
        before = self._snapshot_history_entry()
        edits.split_markers_px = []
        self._push_history_if_changed(before)
        self._set_split_action_mode("none")
        self._refresh_preview()
        self._activate_pan_mode(preserve_status=True)
        self.status_label.setText("Split markers reset to auto-calculated page breaks.")

    def _on_canvas_split_marker_added(self, marker_y: int) -> None:
        if self._current_session() is None:
            return
        markers = self._editable_split_markers_base()
        markers.append(int(marker_y))
        normalized = self._normalized_markers(markers)
        self._set_manual_split_markers(markers, selected_marker=int(marker_y))
        if int(marker_y) in normalized:
            self._set_split_action_mode("none")
            self._activate_pan_mode(preserve_status=True)
            self.status_label.setText(f"Split marker added at Y={int(marker_y)} px.")

    def _on_canvas_split_marker_moved(self, from_y: int, to_y: int) -> None:
        if self._current_session() is None:
            return
        markers = self._editable_split_markers_base()
        try:
            markers.remove(int(from_y))
        except ValueError:
            nearest = self._nearest_effective_split_marker(int(from_y), tolerance_px=18)
            if nearest is None:
                return
            markers = [value for value in markers if int(value) != int(nearest)]
        markers.append(int(to_y))
        self._set_manual_split_markers(markers, selected_marker=int(to_y))

    def _on_canvas_split_marker_removed(self, marker_y: int) -> None:
        if self._current_session() is None:
            return
        nearest = self._nearest_effective_split_marker(int(marker_y), tolerance_px=18)
        if nearest is None:
            self.status_label.setText(
                "No split marker near click. Click a marker line or ruler triangle to remove."
            )
            return
        markers = [value for value in self._editable_split_markers_base() if int(value) != int(nearest)]
        self._set_manual_split_markers(markers)
        self._set_split_action_mode("none")
        self._activate_pan_mode(preserve_status=True)
        self.status_label.setText(f"Split marker removed at Y={int(nearest)} px.")

    def _split_markers(self) -> list[int]:
        edits = self._current_session()
        if edits is None:
            return []
        return self._normalized_markers(edits.split_markers_px)

    def _browse_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder",
            self.output_input.text().strip() or str(Path.home()),
        )
        if selected:
            self.output_input.setText(selected)

    def _run_export(self) -> None:
        self._flush_debounced_preview_update()
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
        header_raw = self.header_input.toHtml()
        footer_raw = self.footer_input.toHtml()
        layout = self._collect_layout()
        if header_raw.strip() and not layout.header_rich_text:
            CAPTURE_UI_LOGGER.info("header rich text ignored: no meaningful content detected")
        if footer_raw.strip() and not layout.footer_rich_text:
            CAPTURE_UI_LOGGER.info("footer rich text ignored: no meaningful content detected")
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
                xlsx=self.xlsx_checkbox.isChecked(),
            ),
            output_dir=Path(self.output_input.text().strip()),
            basename=sanitize_basename(f"{self.base_input.text().strip()}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"),
            docx_pptx_mode=str(self.docx_mode_combo.currentData()),
            layout=layout,
            edits_by_item_id={
                capture.item_id: self._session_for_item(capture.item_id).clone()
                for capture in captures
            },
        )
        selected_formats = [
            name
            for name, enabled in (
                ("pdf", request.formats.pdf),
                ("paged_images", request.formats.paged_images),
                ("long_image", request.formats.long_image),
                ("tiff", request.formats.tiff),
                ("docx", request.formats.docx),
                ("pptx", request.formats.pptx),
                ("xlsx", request.formats.xlsx),
            )
            if enabled
        ]
        if not selected_formats:
            self.status_label.setText("Pick at least one output format.")
            CAPTURE_UI_LOGGER.warning("export blocked: no output formats selected")
            return
        CAPTURE_UI_LOGGER.info(
            "export start formats=%s output_dir=%s basename=%s combine_mode=%s captures=%s",
            ",".join(selected_formats),
            request.output_dir,
            request.basename,
            request.combine_mode,
            len(request.captures),
        )
        try:
            result = run_export(request)
        except Exception as exc:
            CAPTURE_UI_LOGGER.exception(
                "export failed formats=%s output_dir=%s basename=%s",
                ",".join(selected_formats),
                request.output_dir,
                request.basename,
            )
            self.status_label.setText(f"Export failed: {exc}")
            return
        self.status_label.setText(f"Exported {len(result.generated_paths)} file(s).")
        CAPTURE_UI_LOGGER.info(
            "export success generated=%s first_path=%s",
            len(result.generated_paths),
            result.generated_paths[0] if result.generated_paths else "n/a",
        )
        if self.open_after_export_checkbox.isChecked() and result.generated_paths:
            launch_path = (
                result.generated_paths[0]
                if len(result.generated_paths) == 1
                else Path(self.output_input.text().strip())
            )
            with suppress(OSError):
                os.startfile(str(launch_path))

    def _assign_control_identity(self, widget: QWidget, control: str, alias: str) -> None:
        widget_id = widget_naming.control_widget_id(self.window_id, control)
        self._assign_widget_identity(widget, widget_id, alias)

    @staticmethod
    def _assign_widget_identity(widget: QWidget, widget_id: str, alias: str) -> None:
        widget.setObjectName(widget_naming.object_name_for_id(widget_id))
        widget.setProperty("widget_id", widget_id)
        widget.setProperty("widget_alias", alias)

    def closeEvent(self, event) -> None:
        self._flush_debounced_preview_update()
        self._request_stop()
        self._window_state_timer.stop()
        self._splitter_sizes_timer.stop()
        self._persist_window_state_snapshot()
        for key, value in self._collect_settings_payload().items():
            self._settings.setValue(key, value)
        self._settings.sync()
        self._hotkeys.stop()
        self._stop_overlay.hide()
        super().closeEvent(event)
