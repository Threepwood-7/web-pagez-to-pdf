"""Domain models for capture queue and export settings."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


@dataclass(slots=True)
class CaptureItem:
    """One capture/imported image in the working session queue."""

    item_id: str
    title: str
    image_path: Path
    source_hwnd: int | None = None
    frame_count: int | None = None


@dataclass(slots=True)
class ExportFormats:
    """Requested output formats for one export run."""

    pdf: bool = True
    paged_images: bool = False
    long_image: bool = False
    tiff: bool = False
    docx: bool = False
    pptx: bool = False


@dataclass(slots=True)
class PrintLayout:
    """Paper and pagination configuration."""

    paper_name: str = "A4"
    orientation: str = "portrait"
    margin_top_mm: float = 20.0
    margin_bottom_mm: float = 20.0
    margin_left_mm: float = 15.0
    margin_right_mm: float = 15.0
    gutter_mm: float = 0.0
    blank_row_threshold: int = 245
    search_window_px: int = 300
    zoom_percent: float = 100.0
    rotate_degrees: int = 0
    header_rich_text: str = ""
    footer_rich_text: str = ""


@dataclass(slots=True)
class EditOperation:
    """One non-destructive editor operation with typed parameters."""

    op_type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EditAdjustments:
    """Per-item non-destructive edit stack + manual split markers."""

    operations: list[EditOperation] = field(default_factory=list)
    split_markers_px: list[int] = field(default_factory=list)
    crop_left_px: int = 0
    crop_right_px: int = 0
    crop_top_px: int = 0
    crop_bottom_px: int = 0
    auto_crop_left_px: int = 0
    auto_crop_right_px: int = 0

    def clone(self) -> EditAdjustments:
        """Return an isolated deep copy suitable for UI editors."""

        return EditAdjustments(
            operations=[EditOperation(op.op_type, deepcopy(op.params)) for op in self.operations],
            split_markers_px=list(self.split_markers_px),
            crop_left_px=self.crop_left_px,
            crop_right_px=self.crop_right_px,
            crop_top_px=self.crop_top_px,
            crop_bottom_px=self.crop_bottom_px,
            auto_crop_left_px=self.auto_crop_left_px,
            auto_crop_right_px=self.auto_crop_right_px,
        )

    def get_operation(self, op_type: str) -> EditOperation | None:
        """Return first operation for type, if present."""

        key = op_type.strip().lower()
        for operation in self.operations:
            if operation.op_type.strip().lower() == key:
                return operation
        return None

    def set_operation(self, op_type: str, params: dict[str, Any]) -> None:
        """Replace operation of given type with provided payload."""

        key = op_type.strip().lower()
        replacement = EditOperation(op_type=key, params=deepcopy(params))
        for index, operation in enumerate(self.operations):
            if operation.op_type.strip().lower() == key:
                self.operations[index] = replacement
                return
        self.operations.append(replacement)

    def remove_operation(self, op_type: str) -> None:
        """Remove operation of given type if it exists."""

        key = op_type.strip().lower()
        self.operations = [
            operation
            for operation in self.operations
            if operation.op_type.strip().lower() != key
        ]


@dataclass(slots=True)
class EditorSessionState:
    """Session cache of item-specific editor stacks keyed by queue id."""

    edits_by_item_id: dict[str, EditAdjustments] = field(default_factory=dict)


@dataclass(slots=True)
class ExportRequest:
    """Aggregated export request payload."""

    captures: list[CaptureItem]
    combine_mode: bool
    formats: ExportFormats
    output_dir: Path
    basename: str
    docx_pptx_mode: str
    layout: PrintLayout
    edits: EditAdjustments = field(default_factory=EditAdjustments)
    edits_by_item_id: dict[str, EditAdjustments] = field(default_factory=dict)
