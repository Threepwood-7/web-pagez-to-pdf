"""Searchable tree-based settings window for capture/editor/export defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import widget_naming
from .scroll_capture import DEFAULT_SCROLL_STRATEGY


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
        self._assign_identity(self.search_edit, "window:settings:control:search", "settings.search")
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

        self.scroll = QScrollArea(content_host)
        self.scroll.setWidgetResizable(True)
        self.scroll_host = QWidget(self.scroll)
        self.scroll_layout = QVBoxLayout(self.scroll_host)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)
        self.scroll.setWidget(self.scroll_host)
        content_layout.addWidget(self.scroll, 1)

        self.no_matches_label = QLabel("No settings match your search.", self)
        self.no_matches_label.setVisible(False)
        root.addWidget(self.no_matches_label)

        self._build_sections()
        self.search_edit.textChanged.connect(self._apply_search_filter)
        self.section_tree.currentItemChanged.connect(self._on_section_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close,
            parent=self,
        )
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self._emit_apply)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.clicked.connect(self.hide)
        root.addWidget(buttons)

        if self.section_tree.topLevelItemCount() > 0:
            self.section_tree.setCurrentItem(self.section_tree.topLevelItem(0))

    def _build_sections(self) -> None:
        capture_group = self._add_section("capture", "Capture", "capture target stop scroll")
        editor_group = self._add_section("editor", "Editor", "editor crop rotate split redact")
        export_group = self._add_section("export", "Export", "export pdf tiff docx pptx")
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
        self.capture_scroll_strategy_combo = QComboBox(self)
        self.capture_scroll_strategy_combo.addItem("Hybrid Wheel + PageDown", "hybrid_wheel_pagedown")
        self.capture_scroll_strategy_combo.addItem("PageDown only", "pagedown_only")
        self.capture_scroll_strategy_combo.addItem("Wheel only", "wheel_only")
        self._add_row(
            capture_group,
            "capture.scroll_strategy",
            "Scroll Strategy",
            "capture full scroll strategy wheel pagedown hybrid",
            self.capture_scroll_strategy_combo,
        )
        self.auto_pick_second_last_checkbox = QCheckBox(
            "Auto-target second last active window", self
        )
        self._add_row(
            capture_group,
            "capture.auto_pick_second_last",
            "Auto Target",
            "auto target second last active window",
            self.auto_pick_second_last_checkbox,
        )

        self.editor_auto_open_checkbox = QCheckBox(
            "Auto-open mini editor on queue selection", self
        )
        self._add_row(
            editor_group,
            "editor.auto_open_mini",
            "Auto Open Mini Editor",
            "mini editor auto open",
            self.editor_auto_open_checkbox,
        )
        self.editor_show_grid_checkbox = QCheckBox("Show helper grid in mini editor", self)
        self._add_row(
            editor_group,
            "editor.show_grid",
            "Mini Editor Grid",
            "editor grid helper",
            self.editor_show_grid_checkbox,
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

        self.default_pdf_checkbox = QCheckBox("PDF", self)
        self.default_paged_checkbox = QCheckBox("Paged PNG", self)
        self.default_long_checkbox = QCheckBox("Long PNG", self)
        self.default_tiff_checkbox = QCheckBox("TIFF", self)
        self.default_docx_checkbox = QCheckBox("DOCX", self)
        self.default_pptx_checkbox = QCheckBox("PPTX", self)
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
        ):
            formats_layout.addWidget(checkbox)
        self._add_row(
            export_group,
            "export.formats",
            "Default Formats",
            "pdf paged long tiff docx pptx formats",
            formats_row,
        )

        self.start_tab_combo = QComboBox(self)
        self.start_tab_combo.addItem("Capture", "capture")
        self.start_tab_combo.addItem("Editor", "editor")
        self.start_tab_combo.addItem("Export", "export")
        self._add_row(
            ui_group,
            "ui.start_tab",
            "Start Tab",
            "start tab capture editor export",
            self.start_tab_combo,
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

        self.capture_max_pages_spin.setValue(int(values.get("capture.max_pages", 18)))
        self.capture_delay_spin.setValue(int(values.get("capture.delay_ms", 380)))
        self._set_combo_value(
            self.capture_backend_combo,
            str(values.get("capture.backend_primary", "screen_region_gdi")),
        )
        self._set_combo_value(
            self.capture_scroll_strategy_combo,
            str(values.get("capture.scroll_strategy", DEFAULT_SCROLL_STRATEGY)),
        )
        self.auto_pick_second_last_checkbox.setChecked(
            bool(values.get("capture.auto_pick_second_last", True))
        )
        self.editor_auto_open_checkbox.setChecked(bool(values.get("editor.auto_open_mini", False)))
        self.editor_show_grid_checkbox.setChecked(bool(values.get("editor.show_grid", False)))
        self.default_output_edit.setText(str(values.get("export.output_dir", "")))
        self.default_basename_edit.setText(str(values.get("export.basename", "capture")))
        self.default_combine_checkbox.setChecked(bool(values.get("export.combine_mode", True)))
        self.default_pdf_checkbox.setChecked(bool(values.get("export.pdf", True)))
        self.default_paged_checkbox.setChecked(bool(values.get("export.paged_images", False)))
        self.default_long_checkbox.setChecked(bool(values.get("export.long_image", False)))
        self.default_tiff_checkbox.setChecked(bool(values.get("export.tiff", False)))
        self.default_docx_checkbox.setChecked(bool(values.get("export.docx", False)))
        self.default_pptx_checkbox.setChecked(bool(values.get("export.pptx", False)))
        start_tab = str(values.get("ui.start_tab", "capture"))
        self._set_combo_value(self.start_tab_combo, start_tab)
        self.editor_adv_collapsed.setChecked(bool(values.get("ui.editor_adv_collapsed", True)))
        self.export_adv_collapsed.setChecked(bool(values.get("ui.export_adv_collapsed", True)))

    def values(self) -> dict[str, object]:
        """Collect controls into a settings payload."""

        return {
            "capture.max_pages": int(self.capture_max_pages_spin.value()),
            "capture.delay_ms": int(self.capture_delay_spin.value()),
            "capture.backend_primary": str(self.capture_backend_combo.currentData()),
            "capture.scroll_strategy": str(self.capture_scroll_strategy_combo.currentData()),
            "capture.auto_pick_second_last": self.auto_pick_second_last_checkbox.isChecked(),
            "editor.auto_open_mini": self.editor_auto_open_checkbox.isChecked(),
            "editor.show_grid": self.editor_show_grid_checkbox.isChecked(),
            "export.output_dir": self.default_output_edit.text().strip(),
            "export.basename": self.default_basename_edit.text().strip() or "capture",
            "export.combine_mode": self.default_combine_checkbox.isChecked(),
            "export.pdf": self.default_pdf_checkbox.isChecked(),
            "export.paged_images": self.default_paged_checkbox.isChecked(),
            "export.long_image": self.default_long_checkbox.isChecked(),
            "export.tiff": self.default_tiff_checkbox.isChecked(),
            "export.docx": self.default_docx_checkbox.isChecked(),
            "export.pptx": self.default_pptx_checkbox.isChecked(),
            "ui.start_tab": str(self.start_tab_combo.currentData()),
            "ui.editor_adv_collapsed": self.editor_adv_collapsed.isChecked(),
            "ui.export_adv_collapsed": self.export_adv_collapsed.isChecked(),
        }

    def _emit_apply(self) -> None:
        self.settings_applied.emit(self.values())

    def _browse_output(self) -> None:
        root_path = self.default_output_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select Output Folder", root_path)
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
        current = self.section_tree.currentItem()
        if current is not None and not current.isHidden():
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
        self.scroll.ensureWidgetVisible(section.group, 0, 18)

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if str(combo.itemData(index)) == value:
                combo.setCurrentIndex(index)
                return
        combo.setCurrentIndex(0)

    @staticmethod
    def _assign_identity(widget: QWidget, widget_id: str, alias: str) -> None:
        widget.setObjectName(widget_naming.object_name_for_id(widget_id))
        widget.setProperty("widget_id", widget_id)
        widget.setProperty("widget_alias", alias)
