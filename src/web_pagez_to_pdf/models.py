"""Domain models for capture queue and export settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


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
class EditAdjustments:
    """User-selected post-processing transform knobs."""

    crop_left_px: int = 0
    crop_right_px: int = 0
    crop_top_px: int = 0
    crop_bottom_px: int = 0
    split_markers_px: list[int] = field(default_factory=list)
    auto_crop_left_px: int = 0
    auto_crop_right_px: int = 0


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
    edits: EditAdjustments
