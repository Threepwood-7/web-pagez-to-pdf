"""Searchable tree-based settings window for capture/editor/export defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from threep_commons.qt.widget_identity import assign_widget_identity

from .capture_service import CAPTURE_FRAME_REGIONS, DEFAULT_CAPTURE_FRAME_REGION
from .image_processing import PAPER_SIZES
from .scroll_capture import (
    DEFAULT_AUTO_TRIM_FIXED_STRIPS,
    DEFAULT_CAPTURE_LOG_LEVEL,
    DEFAULT_CURSOR_HOLD_MODE,
    DEFAULT_SCROLL_MODE,
    DEFAULT_WHEEL_INJECTION_MODE,
    SCROLL_MODES,
    normalize_capture_log_level,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class _RowEntry:
    key: str
    widget: QWidget
    terms: str


@dataclass
class _SectionEntry:
    key: str
    group: QGroupBox
    rows: list[_RowEntry]
    terms: str


class SettingsWindow(QDialog):
    """Modeless settings dialog with section tree and search filter."""

    settings_applied = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sections: dict[str, _SectionEntry] = {}
        self._section_tree_items: dict[str, QTreeWidgetItem] = {}
        self.setModal(False)
        self.setWindowTitle("Settings")
        self.resize(1060, 760)
        self.setMinimumSize(940, 640)
        self._assign_identity(self, "window:settings", "window.settings")
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Search settings...")
        self.search_edit.setClearButtonEnabled(True)
        self._assign_identity(
            self.search_edit, "window:settings:control:search", "settings.search"
        )
        root.addWidget(self.search_edit)

        content_host = QWidget(self)
        content_layout = QHBoxLayout(content_host)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        root.addWidget(content_host, 1)

        self.section_tree = QTreeWidget(content_host)
        self.section_tree.setHeaderHidden(True)
        self.section_tree.setMinimumWidth(210)
        self.section_tree.setMaximumWidth(270)
        self._assign_identity(
            self.section_tree,
            "window:settings:control:section_tree",
            "settings.section_tree",
        )
        content_layout.addWidget(self.section_tree, 0)

        self.scroll_area = QScrollArea(content_host)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_host = QWidget(self.scroll_area)
        self.scroll_layout = QVBoxLayout(self.scroll_host)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)
        self.scroll_area.setWidget(self.scroll_host)
        content_layout.addWidget(self.scroll_area, 1)

        self.no_matches_label = QLabel("No settings match your search.", self)
        self.no_matches_label.setVisible(False)
        root.addWidget(self.no_matches_label)

        self._build_sections()
        self.search_edit.textChanged.connect(self._apply_search_filter)
        self.section_tree.currentItemChanged.connect(self._on_section_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Close,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self._emit_apply
        )
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.hide)
        root.addWidget(buttons)

        if self.section_tree.topLevelItemCount() > 0:
            first_item: QTreeWidgetItem | None = self.section_tree.topLevelItem(0)
            if first_item is not None:
                self.section_tree.setCurrentItem(first_item)

    def _build_sections(self) -> None:
        capture_group = self._add_section(
            "capture", "Capture", "capture target stop scroll"
        )
        export_group = self._add_section(
            "export", "Export", "export pdf tiff docx pptx xlsx"
        )
        layout_group = self._add_section(
            "layout", "Layout", "layout paper margins header footer"
        )
        editor_group = self._add_section("editor", "Editor", "editor overlay debounce")
        ui_group = self._add_section("ui", "UI", "ui tab collapse")

        self.capture_max_pages_spin = QSpinBox(self)
        self.capture_max_pages_spin.setRange(2, 500)
        self._add_row(
            capture_group,
            "capture.max_pages",
            "Default Max Pages",
            "capture max pages full scroll limit",
            self.capture_max_pages_spin,
        )
        self.capture_delay_spin = QSpinBox(self)
        self.capture_delay_spin.setRange(120, 2000)
        self.capture_delay_spin.setSuffix(" ms")
        self._add_row(
            capture_group,
            "capture.delay_ms",
            "Scroll Delay",
            "capture delay ms page-down wait",
            self.capture_delay_spin,
        )
        self.capture_backend_combo = QComboBox(self)
        self.capture_backend_combo.addItem("Screen Region (GDI)", "screen_region_gdi")
        self.capture_backend_combo.addItem("Qt grabWindow", "qt_grab_window")
        self.capture_backend_combo.addItem("PrintWindow", "print_window")
        self._add_row(
            capture_group,
            "capture.backend_primary",
            "Capture Backend",
            "capture backend gdi qt printwindow",
            self.capture_backend_combo,
        )
        self.capture_scroll_mode_combo = QComboBox(self)
        self.capture_scroll_mode_combo.addItem(
            "Wheel then PageDown", "wheel_then_pagedown"
        )
        self.capture_scroll_mode_combo.addItem("Wheel", "wheel_only")
        self.capture_scroll_mode_combo.addItem("Wheel + Click", "wheel_click")
        self.capture_scroll_mode_combo.addItem("Wheel + PageDown", "wheel_pagedown")
        self.capture_scroll_mode_combo.addItem(
            "Wheel + Click + PageDown", "wheel_click_pagedown"
        )
        self._add_row(
            capture_group,
            "capture.scroll_mode",
            "Scroll Mode",
            "capture full scroll mode wheel click pagedown",
            self.capture_scroll_mode_combo,
        )
        self.capture_scroll_to_top_checkbox = QCheckBox(
            "Scroll to Top before full capture",
            self,
        )
        self._add_row(
            capture_group,
            "capture.scroll_to_top_on_full",
            "Scroll To Top",
            "capture scroll to top before full capture wheel up home",
            self.capture_scroll_to_top_checkbox,
        )
        self.capture_auto_trim_fixed_checkbox = QCheckBox(
            "Auto-trim fixed top/bottom strips",
            self,
        )
        self._add_row(
            capture_group,
            "capture.auto_trim_fixed_strips",
            "Auto Trim Fixed Strips",
            "capture auto trim fixed strips sticky top bottom",
            self.capture_auto_trim_fixed_checkbox,
        )
        self.capture_frame_region_combo = QComboBox(self)
        self.capture_frame_region_combo.addItem(
            "Client Area (No Border)", "client_area"
        )
        self.capture_frame_region_combo.addItem(
            "Full Window (Border + Title Bar)", "full_window"
        )
        self._add_row(
            capture_group,
            "capture.frame_region",
            "Frame Region",
            "capture frame region client area full window border",
            self.capture_frame_region_combo,
        )
        self.capture_wheel_injection_combo = QComboBox(self)
        self.capture_wheel_injection_combo.addItem(
            "Physical Center (SendInput)",
            "physical_center_sendinput",
        )
        self.capture_wheel_injection_combo.addItem(
            "Legacy WM_MOUSEWHEEL",
            "legacy_message_wheel",
        )
        self._add_row(
            capture_group,
            "capture.wheel_injection_mode",
            "Wheel Injection",
            "capture wheel injection sendinput legacy message",
            self.capture_wheel_injection_combo,
        )
        self.capture_cursor_hold_combo = QComboBox(self)
        self.capture_cursor_hold_combo.addItem("Keep At Center", "keep_at_center")
        self.capture_cursor_hold_combo.addItem("Restore Each Step", "restore_each_step")
        self._add_row(
            capture_group,
            "capture.cursor_hold_mode",
            "Cursor Hold",
            "capture cursor hold keep center restore each step",
            self.capture_cursor_hold_combo,
        )
        self.capture_log_level_combo = QComboBox(self)
        self.capture_log_level_combo.addItem("INFO", "INFO")
        self.capture_log_level_combo.addItem("DEBUG", "DEBUG")
        self._add_row(
            capture_group,
            "capture.log_level",
            "Diagnostics Log Level",
            "capture diagnostics log level info debug",
            self.capture_log_level_combo,
        )
        self.capture_include_mouse_checkbox = QCheckBox("Capture mouse cursor", self)
        self._add_row(
            capture_group,
            "capture.include_mouse_cursor",
            "Include Mouse Cursor",
            "capture mouse cursor include",
            self.capture_include_mouse_checkbox,
        )

        output_row = QWidget(self)
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        self.default_output_edit = QLineEdit(self)
        self.default_output_browse = QPushButton("Browse", self)
        self.default_output_browse.clicked.connect(self._browse_output)
        output_layout.addWidget(self.default_output_edit, 1)
        output_layout.addWidget(self.default_output_browse, 0)
        self._add_row(
            export_group,
            "export.output_dir",
            "Default Output Folder",
            "export output folder path",
            output_row,
        )
        self.default_basename_edit = QLineEdit(self)
        self._add_row(
            export_group,
            "export.basename",
            "Default Base Name",
            "export basename filename",
            self.default_basename_edit,
        )
        self.default_combine_checkbox = QCheckBox("Combine queue by default", self)
        self._add_row(
            export_group,
            "export.combine_mode",
            "Combine Queue",
            "combine queue default",
            self.default_combine_checkbox,
        )
        self.default_open_after_export_checkbox = QCheckBox(
            "Open file after export", self
        )
        self._add_row(
            export_group,
            "export.open_after_export",
            "Open After Export",
            "open file folder launch after export",
            self.default_open_after_export_checkbox,
        )

        self.default_pdf_checkbox = QCheckBox("PDF", self)
        self.default_paged_checkbox = QCheckBox("Paged PNG", self)
        self.default_long_checkbox = QCheckBox("Long PNG", self)
        self.default_tiff_checkbox = QCheckBox("TIFF", self)
        self.default_docx_checkbox = QCheckBox("DOCX", self)
        self.default_pptx_checkbox = QCheckBox("PPTX", self)
        self.default_xlsx_checkbox = QCheckBox("Excel (XLSX)", self)
        formats_row = QWidget(self)
        formats_layout = QHBoxLayout(formats_row)
        formats_layout.setContentsMargins(0, 0, 0, 0)
        formats_layout.setSpacing(6)
        for checkbox in (
            self.default_pdf_checkbox,
            self.default_paged_checkbox,
            self.default_long_checkbox,
            self.default_tiff_checkbox,
            self.default_docx_checkbox,
            self.default_pptx_checkbox,
            self.default_xlsx_checkbox,
        ):
            formats_layout.addWidget(checkbox)
        self._add_row(
            export_group,
            "export.formats",
            "Default Formats",
            "pdf paged long tiff docx pptx xlsx formats",
            formats_row,
        )
        self.default_docx_mode_combo = QComboBox(self)
        self.default_docx_mode_combo.addItem("Per split-page", "per_split_page")
        self.default_docx_mode_combo.addItem("Per capture", "per_capture")
        self._add_row(
            export_group,
            "export.docx_mode",
            "DOCX/PPTX/XLSX Mode",
            "docx pptx xlsx mode per split page per capture",
            self.default_docx_mode_combo,
        )

        self.layout_paper_combo = QComboBox(self)
        for paper_name in sorted(PAPER_SIZES.keys()):
            self.layout_paper_combo.addItem(paper_name, paper_name)
        self._add_row(
            layout_group,
            "layout.paper_name",
            "Paper",
            "paper size a4 letter legal tabloid",
            self.layout_paper_combo,
        )
        self.layout_orientation_combo = QComboBox(self)
        self.layout_orientation_combo.addItem("portrait", "portrait")
        self.layout_orientation_combo.addItem("landscape", "landscape")
        self._add_row(
            layout_group,
            "layout.orientation",
            "Orientation",
            "layout orientation portrait landscape",
            self.layout_orientation_combo,
        )
        self.layout_margin_top_spin = QDoubleSpinBox(self)
        self.layout_margin_top_spin.setRange(0.0, 120.0)
        self.layout_margin_top_spin.setDecimals(1)
        self.layout_margin_top_spin.setSuffix(" mm")
        self._add_row(
            layout_group,
            "layout.margin_top_mm",
            "Top Margin",
            "layout margin top mm",
            self.layout_margin_top_spin,
        )
        self.layout_margin_bottom_spin = QDoubleSpinBox(self)
        self.layout_margin_bottom_spin.setRange(0.0, 120.0)
        self.layout_margin_bottom_spin.setDecimals(1)
        self.layout_margin_bottom_spin.setSuffix(" mm")
        self._add_row(
            layout_group,
            "layout.margin_bottom_mm",
            "Bottom Margin",
            "layout margin bottom mm",
            self.layout_margin_bottom_spin,
        )
        self.layout_margin_left_spin = QDoubleSpinBox(self)
        self.layout_margin_left_spin.setRange(0.0, 120.0)
        self.layout_margin_left_spin.setDecimals(1)
        self.layout_margin_left_spin.setSuffix(" mm")
        self._add_row(
            layout_group,
            "layout.margin_left_mm",
            "Left Margin",
            "layout margin left mm",
            self.layout_margin_left_spin,
        )
        self.layout_margin_right_spin = QDoubleSpinBox(self)
        self.layout_margin_right_spin.setRange(0.0, 120.0)
        self.layout_margin_right_spin.setDecimals(1)
        self.layout_margin_right_spin.setSuffix(" mm")
        self._add_row(
            layout_group,
            "layout.margin_right_mm",
            "Right Margin",
            "layout margin right mm",
            self.layout_margin_right_spin,
        )
        self.layout_gutter_spin = QDoubleSpinBox(self)
        self.layout_gutter_spin.setRange(0.0, 80.0)
        self.layout_gutter_spin.setDecimals(1)
        self.layout_gutter_spin.setSuffix(" mm")
        self._add_row(
            layout_group,
            "layout.gutter_mm",
            "Gutter",
            "layout gutter mm",
            self.layout_gutter_spin,
        )
        self.layout_blank_threshold_spin = QSpinBox(self)
        self.layout_blank_threshold_spin.setRange(0, 255)
        self._add_row(
            layout_group,
            "layout.blank_row_threshold",
            "Blank Row Threshold",
            "layout blank threshold split detection",
            self.layout_blank_threshold_spin,
        )
        self.layout_search_window_spin = QSpinBox(self)
        self.layout_search_window_spin.setRange(1, 5000)
        self.layout_search_window_spin.setSuffix(" px")
        self._add_row(
            layout_group,
            "layout.search_window_px",
            "Search Window",
            "layout search window px page split",
            self.layout_search_window_spin,
        )
        self.layout_header_edit = QTextEdit(self)
        self.layout_header_edit.setMinimumHeight(90)
        self._add_row(
            layout_group,
            "layout.header_html",
            "Header (Rich Text)",
            "layout header html rich text page title tokens",
            self.layout_header_edit,
        )
        self.layout_footer_edit = QTextEdit(self)
        self.layout_footer_edit.setMinimumHeight(90)
        self._add_row(
            layout_group,
            "layout.footer_html",
            "Footer (Rich Text)",
            "layout footer html rich text page title tokens",
            self.layout_footer_edit,
        )

        self.editor_overlay_visible_checkbox = QCheckBox(
            "Show split/page overlays", self
        )
        self._add_row(
            editor_group,
            "ui.editor_overlay_visible",
            "Overlay Visibility",
            "editor overlay split marker guides",
            self.editor_overlay_visible_checkbox,
        )
        self.editor_preview_debounce_spin = QSpinBox(self)
        self.editor_preview_debounce_spin.setRange(0, 2000)
        self.editor_preview_debounce_spin.setSuffix(" ms")
        self._add_row(
            editor_group,
            "editor.preview_debounce_ms",
            "Preview Debounce",
            "editor debounce preview transform layout refresh",
            self.editor_preview_debounce_spin,
        )

        self.editor_adv_collapsed = QCheckBox("Editor advanced collapsed", self)
        self.export_adv_collapsed = QCheckBox("Export print collapsed", self)
        collapse_row = QWidget(self)
        collapse_layout = QHBoxLayout(collapse_row)
        collapse_layout.setContentsMargins(0, 0, 0, 0)
        collapse_layout.setSpacing(6)
        collapse_layout.addWidget(self.editor_adv_collapsed)
        collapse_layout.addWidget(self.export_adv_collapsed)
        self._add_row(
            ui_group,
            "ui.collapse_defaults",
            "Collapse Defaults",
            "collapse advanced defaults",
            collapse_row,
        )

    def set_values(self, values: dict[str, object]) -> None:
        """Load settings payload into controls."""

        self.capture_max_pages_spin.setValue(
            self._int_setting(values, "capture.max_pages", 50)
        )
        self.capture_delay_spin.setValue(
            self._int_setting(values, "capture.delay_ms", 333)
        )
        self._set_combo_value(
            self.capture_backend_combo,
            str(values.get("capture.backend_primary", "screen_region_gdi")),
        )
        self._set_combo_value(
            self.capture_scroll_mode_combo,
            str(values.get("capture.scroll_mode", DEFAULT_SCROLL_MODE)),
        )
        self.capture_scroll_to_top_checkbox.setChecked(
            bool(values.get("capture.scroll_to_top_on_full", True))
        )
        self.capture_auto_trim_fixed_checkbox.setChecked(
            bool(
                values.get(
                    "capture.auto_trim_fixed_strips",
                    DEFAULT_AUTO_TRIM_FIXED_STRIPS,
                )
            )
        )
        self._set_combo_value(
            self.capture_frame_region_combo,
            str(values.get("capture.frame_region", DEFAULT_CAPTURE_FRAME_REGION)),
        )
        self._set_combo_value(
            self.capture_wheel_injection_combo,
            str(
                values.get("capture.wheel_injection_mode", DEFAULT_WHEEL_INJECTION_MODE)
            ),
        )
        self._set_combo_value(
            self.capture_cursor_hold_combo,
            str(values.get("capture.cursor_hold_mode", DEFAULT_CURSOR_HOLD_MODE)),
        )
        self._set_combo_value(
            self.capture_log_level_combo,
            normalize_capture_log_level(
                str(values.get("capture.log_level", DEFAULT_CAPTURE_LOG_LEVEL))
            ),
        )
        self.capture_include_mouse_checkbox.setChecked(
            bool(values.get("capture.include_mouse_cursor", False))
        )
        self.default_output_edit.setText(str(values.get("export.output_dir", "")))
        self.default_basename_edit.setText(
            str(values.get("export.basename", "capture"))
        )
        self.default_combine_checkbox.setChecked(
            bool(values.get("export.combine_mode", True))
        )
        self.default_pdf_checkbox.setChecked(bool(values.get("export.pdf", True)))
        self.default_paged_checkbox.setChecked(
            bool(values.get("export.paged_images", False))
        )
        self.default_long_checkbox.setChecked(
            bool(values.get("export.long_image", False))
        )
        self.default_tiff_checkbox.setChecked(bool(values.get("export.tiff", False)))
        self.default_docx_checkbox.setChecked(bool(values.get("export.docx", False)))
        self.default_pptx_checkbox.setChecked(bool(values.get("export.pptx", False)))
        self.default_xlsx_checkbox.setChecked(bool(values.get("export.xlsx", False)))
        self.default_open_after_export_checkbox.setChecked(
            bool(values.get("export.open_after_export", True))
        )
        self._set_combo_value(
            self.default_docx_mode_combo,
            str(values.get("export.docx_mode", "per_split_page")),
        )
        self._set_combo_value(
            self.layout_paper_combo,
            str(values.get("layout.paper_name", "A4")),
        )
        self._set_combo_value(
            self.layout_orientation_combo,
            str(values.get("layout.orientation", "portrait")),
        )
        self.layout_margin_top_spin.setValue(
            self._float_setting(values, "layout.margin_top_mm", 20.0)
        )
        self.layout_margin_bottom_spin.setValue(
            self._float_setting(values, "layout.margin_bottom_mm", 20.0)
        )
        self.layout_margin_left_spin.setValue(
            self._float_setting(values, "layout.margin_left_mm", 15.0)
        )
        self.layout_margin_right_spin.setValue(
            self._float_setting(values, "layout.margin_right_mm", 15.0)
        )
        self.layout_gutter_spin.setValue(
            self._float_setting(values, "layout.gutter_mm", 0.0)
        )
        self.layout_blank_threshold_spin.setValue(
            self._int_setting(values, "layout.blank_row_threshold", 245)
        )
        self.layout_search_window_spin.setValue(
            self._int_setting(values, "layout.search_window_px", 300)
        )
        self.layout_header_edit.setHtml(str(values.get("layout.header_html", "")))
        self.layout_footer_edit.setHtml(str(values.get("layout.footer_html", "")))
        self.editor_overlay_visible_checkbox.setChecked(
            bool(values.get("ui.editor_overlay_visible", True))
        )
        debounce_value = self._int_setting(values, "editor.preview_debounce_ms", 333)
        self.editor_preview_debounce_spin.setValue(max(0, min(2000, debounce_value)))
        self.editor_adv_collapsed.setChecked(
            bool(values.get("ui.editor_adv_collapsed", True))
        )
        self.export_adv_collapsed.setChecked(
            bool(values.get("ui.export_adv_collapsed", True))
        )

    def values(self) -> dict[str, object]:
        """Collect controls into a settings payload."""

        return {
            "capture.max_pages": int(self.capture_max_pages_spin.value()),
            "capture.delay_ms": int(self.capture_delay_spin.value()),
            "capture.backend_primary": str(self.capture_backend_combo.currentData()),
            "capture.scroll_mode": self._scroll_mode_value(),
            "capture.scroll_to_top_on_full": (
                self.capture_scroll_to_top_checkbox.isChecked()
            ),
            "capture.auto_trim_fixed_strips": (
                self.capture_auto_trim_fixed_checkbox.isChecked()
            ),
            "capture.frame_region": self._frame_region_value(),
            "capture.wheel_injection_mode": str(
                self.capture_wheel_injection_combo.currentData()
            ),
            "capture.cursor_hold_mode": str(
                self.capture_cursor_hold_combo.currentData()
            ),
            "capture.log_level": normalize_capture_log_level(
                str(self.capture_log_level_combo.currentData())
            ),
            "capture.include_mouse_cursor": (
                self.capture_include_mouse_checkbox.isChecked()
            ),
            "export.output_dir": self.default_output_edit.text().strip(),
            "export.basename": self.default_basename_edit.text().strip() or "capture",
            "export.combine_mode": self.default_combine_checkbox.isChecked(),
            "export.pdf": self.default_pdf_checkbox.isChecked(),
            "export.paged_images": self.default_paged_checkbox.isChecked(),
            "export.long_image": self.default_long_checkbox.isChecked(),
            "export.tiff": self.default_tiff_checkbox.isChecked(),
            "export.docx": self.default_docx_checkbox.isChecked(),
            "export.pptx": self.default_pptx_checkbox.isChecked(),
            "export.xlsx": self.default_xlsx_checkbox.isChecked(),
            "export.open_after_export": (
                self.default_open_after_export_checkbox.isChecked()
            ),
            "export.docx_mode": str(self.default_docx_mode_combo.currentData()),
            "layout.paper_name": str(self.layout_paper_combo.currentData() or "A4"),
            "layout.orientation": str(
                self.layout_orientation_combo.currentData() or "portrait"
            ),
            "layout.margin_top_mm": float(self.layout_margin_top_spin.value()),
            "layout.margin_bottom_mm": float(self.layout_margin_bottom_spin.value()),
            "layout.margin_left_mm": float(self.layout_margin_left_spin.value()),
            "layout.margin_right_mm": float(self.layout_margin_right_spin.value()),
            "layout.gutter_mm": float(self.layout_gutter_spin.value()),
            "layout.blank_row_threshold": int(self.layout_blank_threshold_spin.value()),
            "layout.search_window_px": int(self.layout_search_window_spin.value()),
            "layout.header_html": self.layout_header_edit.toHtml(),
            "layout.footer_html": self.layout_footer_edit.toHtml(),
            "ui.editor_overlay_visible": (
                self.editor_overlay_visible_checkbox.isChecked()
            ),
            "editor.preview_debounce_ms": int(
                self.editor_preview_debounce_spin.value()
            ),
            "ui.editor_adv_collapsed": self.editor_adv_collapsed.isChecked(),
            "ui.export_adv_collapsed": self.export_adv_collapsed.isChecked(),
        }

    def _scroll_mode_value(self) -> str:
        value = str(self.capture_scroll_mode_combo.currentData())
        if value in SCROLL_MODES:
            return value
        return DEFAULT_SCROLL_MODE

    def _frame_region_value(self) -> str:
        value = str(self.capture_frame_region_combo.currentData())
        if value in CAPTURE_FRAME_REGIONS:
            return value
        return DEFAULT_CAPTURE_FRAME_REGION

    def _emit_apply(self) -> None:
        self.settings_applied.emit(self.values())

    def _browse_output(self) -> None:
        root_path = self.default_output_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self, "Select Output Folder", root_path
        )
        if selected:
            self.default_output_edit.setText(selected)

    def _add_section(self, key: str, title: str, terms: str) -> _SectionEntry:
        group = QGroupBox(title, self.scroll_host)
        layout = QFormLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.scroll_layout.addWidget(group)
        section = _SectionEntry(key=key, group=group, rows=[], terms=terms.casefold())
        self._sections[key] = section

        tree_item = QTreeWidgetItem([title])
        tree_item.setData(0, Qt.ItemDataRole.UserRole, key)
        self.section_tree.addTopLevelItem(tree_item)
        self._section_tree_items[key] = tree_item
        return section

    def _add_row(
        self,
        section: _SectionEntry,
        key: str,
        title: str,
        terms: str,
        control: QWidget,
    ) -> None:
        holder = QWidget(section.group)
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(control)
        form = section.group.layout()
        if isinstance(form, QFormLayout):
            form.addRow(title, holder)
        section.rows.append(_RowEntry(key=key, widget=holder, terms=terms.casefold()))

    def _apply_search_filter(self, text: str) -> None:
        query = str(text or "").strip().casefold()
        visible_rows = 0
        for key, section in self._sections.items():
            section_hit = bool(query) and query in section.terms
            section_visible_rows = 0
            for row in section.rows:
                row_visible = (not query) or section_hit or (query in row.terms)
                row.widget.setVisible(row_visible)
                if row_visible:
                    section_visible_rows += 1
            section_visible = section_visible_rows > 0
            section.group.setVisible(section_visible)
            tree_item = self._section_tree_items.get(key)
            if tree_item is not None:
                tree_item.setHidden(not section_visible)
            visible_rows += section_visible_rows
        self.no_matches_label.setVisible(bool(query) and visible_rows == 0)
        self._ensure_visible_section_selection()

    def _ensure_visible_section_selection(self) -> None:
        selected_items = self.section_tree.selectedItems()
        if selected_items and not selected_items[0].isHidden():
            return
        for index in range(self.section_tree.topLevelItemCount()):
            item = self.section_tree.topLevelItem(index)
            if item is None or item.isHidden():
                continue
            self.section_tree.setCurrentItem(item)
            key = str(item.data(0, Qt.ItemDataRole.UserRole))
            self._scroll_to_section(key)
            return

    def _on_section_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if current is None or current.isHidden():
            return
        key = str(current.data(0, Qt.ItemDataRole.UserRole))
        self._scroll_to_section(key)

    def _scroll_to_section(self, key: str) -> None:
        section = self._sections.get(key)
        if section is None or not section.group.isVisible():
            return
        self.scroll_area.ensureWidgetVisible(section.group, 0, 18)

    @staticmethod
    def _int_setting(values: Mapping[str, object], key: str, default: int) -> int:
        """Coerce integer settings loaded from loosely typed JSON-like payloads."""

        value = values.get(key, default)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            try:
                return int(float(stripped))
            except ValueError:
                return default
        return default

    @staticmethod
    def _float_setting(values: Mapping[str, object], key: str, default: float) -> float:
        """Coerce float settings loaded from loosely typed JSON-like payloads."""

        value = values.get(key, default)
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            try:
                return float(stripped)
            except ValueError:
                return default
        return default

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if str(combo.itemData(index)) == value:
                combo.setCurrentIndex(index)
                return
        combo.setCurrentIndex(0)

    @staticmethod
    def _assign_identity(widget: QWidget, widget_id: str, alias: str) -> None:
        assign_widget_identity(widget, widget_id=widget_id, widget_alias=alias)
