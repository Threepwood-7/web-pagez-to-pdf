"""Export writers for PDF/images/TIFF/DOCX/PPTX from capture session data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from .image_processing import (
    PAPER_SIZES,
    PageSlice,
    apply_edit_transform,
    compute_page_slices,
)
from .models import EditAdjustments

if TYPE_CHECKING:
    from .models import CaptureItem, ExportRequest

try:
    from docx import Document
    from docx.shared import Mm
except ImportError:  # pragma: no cover
    Document = None  # type: ignore[assignment]
    Mm = None  # type: ignore[assignment]

try:
    from pptx import Presentation
    from pptx.util import Inches
except ImportError:  # pragma: no cover
    Presentation = None  # type: ignore[assignment]
    Inches = None  # type: ignore[assignment]


@dataclass(slots=True)
class PageFrame:
    """Renderable page frame with source metadata."""

    image: Image.Image
    title: str
    source_item: CaptureItem
    page_index_in_item: int
    page_count_in_item: int


@dataclass(slots=True)
class ExportResult:
    """Paths written by one export invocation."""

    generated_paths: list[Path]


def run_export(request: ExportRequest) -> ExportResult:
    """Export selected capture queue into requested output formats."""

    request.output_dir = Path(request.output_dir)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    frames, transformed_images = build_page_frames(request)
    if not frames:
        raise RuntimeError("No exportable frames are available.")

    generated: list[Path] = []
    if request.formats.pdf:
        generated.append(export_pdf(request, frames))
    if request.formats.paged_images:
        generated.extend(export_paged_images(request, frames))
    if request.formats.long_image:
        generated.append(export_long_image(request, transformed_images))
    if request.formats.tiff:
        generated.append(export_multipage_tiff(request, frames))
    if request.formats.docx:
        generated.append(export_docx(request, frames, transformed_images))
    if request.formats.pptx:
        generated.append(export_pptx(request, frames, transformed_images))
    return ExportResult(generated_paths=generated)


def build_page_frames(request: ExportRequest) -> tuple[list[PageFrame], list[tuple[CaptureItem, Image.Image]]]:
    """Build transformed images and split frames according to export mode."""

    transformed_images: list[tuple[CaptureItem, Image.Image]] = []
    for item in request.captures:
        image = Image.open(item.image_path).convert("RGB")
        edits = _edits_for_item(request, item)
        transformed = apply_edit_transform(image, request.layout, edits)
        transformed_images.append((item, transformed))

    frames: list[PageFrame] = []
    if request.combine_mode:
        for item, image in transformed_images:
            edits = _edits_for_item(request, item)
            slices = compute_page_slices(image, request.layout, edits.split_markers_px)
            frames.extend(_frames_for_slices(item, image, slices))
    else:
        first_item, first_image = transformed_images[0]
        edits = _edits_for_item(request, first_item)
        slices = compute_page_slices(first_image, request.layout, edits.split_markers_px)
        frames.extend(_frames_for_slices(first_item, first_image, slices))
    return (frames, transformed_images)


def _edits_for_item(request: ExportRequest, item: CaptureItem) -> EditAdjustments:
    return request.edits_by_item_id.get(item.item_id) or request.edits or EditAdjustments()


def _frames_for_slices(item: CaptureItem, image: Image.Image, slices: list[PageSlice]) -> list[PageFrame]:
    frames: list[PageFrame] = []
    page_total = len(slices)
    for idx, slice_info in enumerate(slices):
        frame = image.crop((0, slice_info.top, image.width, slice_info.bottom))
        frames.append(
            PageFrame(
                image=frame,
                title=item.title,
                source_item=item,
                page_index_in_item=idx + 1,
                page_count_in_item=page_total,
            )
        )
    return frames


def export_pdf(request: ExportRequest, frames: list[PageFrame]) -> Path:
    """Write PDF with optional rich header/footer."""

    page_size = PAPER_SIZES.get(request.layout.paper_name.upper(), A4)
    page_w, page_h = page_size
    if request.layout.orientation.lower() == "landscape":
        page_w, page_h = page_h, page_w

    output_path = request.output_dir / f"{request.basename}.pdf"
    pdf = canvas.Canvas(str(output_path), pagesize=(page_w, page_h))
    total_pages = len(frames)
    avail_w = page_w - (request.layout.margin_left_mm + request.layout.margin_right_mm + request.layout.gutter_mm) * mm
    for page_index, frame in enumerate(frames, start=1):
        scale = avail_w / float(frame.image.width)
        rendered_h = frame.image.height * scale
        y = page_h - request.layout.margin_top_mm * mm - rendered_h
        pdf.drawInlineImage(frame.image, request.layout.margin_left_mm * mm, y, width=avail_w, height=rendered_h)

        context = {
            "title": frame.title,
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "page": str(page_index),
            "pages": str(total_pages),
        }
        _draw_rich_text(pdf, request.layout.header_rich_text, context, request.layout.margin_left_mm * mm, page_h - 16, avail_w)
        _draw_rich_text(pdf, request.layout.footer_rich_text, context, request.layout.margin_left_mm * mm, 12, avail_w)
        if page_index < total_pages:
            pdf.showPage()
            pdf.setPageSize((page_w, page_h))
    pdf.save()
    return output_path


def _draw_rich_text(pdf: canvas.Canvas, rich_text: str, context: dict[str, str], x_pos: float, y_pos: float, width: float) -> None:
    text = _apply_tokens(rich_text, context).strip()
    if not text:
        return
    paragraph = Paragraph(text)
    wrapped_w, wrapped_h = paragraph.wrap(width, 60)
    paragraph.drawOn(pdf, x_pos, y_pos - wrapped_h)
    if wrapped_w <= 0:
        return


def export_paged_images(request: ExportRequest, frames: list[PageFrame]) -> list[Path]:
    """Export each page frame as numbered PNG files."""

    generated: list[Path] = []
    for index, frame in enumerate(frames, start=1):
        filename = f"{request.basename}_p{index:03d}.png"
        path = request.output_dir / filename
        frame.image.save(path, format="PNG")
        generated.append(path)
    return generated


def export_long_image(request: ExportRequest, transformed: list[tuple[CaptureItem, Image.Image]]) -> Path:
    """Export one long stitched image for selected captures."""

    if request.combine_mode:
        width = max(img.width for _item, img in transformed)
        total_height = sum(img.height for _item, img in transformed) + max(0, len(transformed) - 1) * 16
        canvas_img = Image.new("RGB", (width, total_height), "white")
        y_pos = 0
        for _item, image in transformed:
            canvas_img.paste(image, (0, y_pos))
            y_pos += image.height + 16
    else:
        canvas_img = transformed[0][1]
    output = request.output_dir / f"{request.basename}_long.png"
    canvas_img.save(output, format="PNG")
    return output


def export_multipage_tiff(request: ExportRequest, frames: list[PageFrame]) -> Path:
    """Export split pages into one multi-page TIFF file."""

    output = request.output_dir / f"{request.basename}.tiff"
    head = frames[0].image
    tail = [frame.image for frame in frames[1:]]
    head.save(output, format="TIFF", save_all=True, append_images=tail, compression="tiff_deflate")
    return output


def export_docx(
    request: ExportRequest,
    frames: list[PageFrame],
    transformed: list[tuple[CaptureItem, Image.Image]],
) -> Path:
    """Export images into DOCX with user-selected layout mode."""

    if Document is None or Mm is None:  # pragma: no cover
        raise RuntimeError("python-docx dependency is required for DOCX export.")
    document = Document()
    output = request.output_dir / f"{request.basename}.docx"
    mode = request.docx_pptx_mode.strip().lower()
    if mode == "per_capture":
        sources = [image for _item, image in transformed]
    else:
        sources = [frame.image for frame in frames]
    for index, image in enumerate(sources):
        if index > 0:
            document.add_page_break()
        paragraph = document.add_paragraph()
        with BytesIO() as buffer:
            image.save(buffer, format="PNG")
            buffer.seek(0)
            paragraph.add_run().add_picture(buffer, width=Mm(170))
    document.save(output)
    return output


def export_pptx(
    request: ExportRequest,
    frames: list[PageFrame],
    transformed: list[tuple[CaptureItem, Image.Image]],
) -> Path:
    """Export images into PPTX slides with selected layout mode."""

    if Presentation is None or Inches is None:  # pragma: no cover
        raise RuntimeError("python-pptx dependency is required for PPTX export.")
    presentation = Presentation()
    output = request.output_dir / f"{request.basename}.pptx"
    mode = request.docx_pptx_mode.strip().lower()
    if mode == "per_capture":
        sources = [image for _item, image in transformed]
    else:
        sources = [frame.image for frame in frames]
    for image in sources:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        with BytesIO() as buffer:
            image.save(buffer, format="PNG")
            buffer.seek(0)
            slide.shapes.add_picture(buffer, Inches(0.3), Inches(0.3), width=Inches(12.7))
    presentation.save(output)
    return output


def sanitize_basename(value: str) -> str:
    """Filesystem-safe export base stem."""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    if not cleaned:
        return "capture"
    return cleaned[:140]


def _apply_tokens(text: str, context: dict[str, str]) -> str:
    result = text
    for key, value in context.items():
        result = result.replace(f"{{{key}}}", value)
    return result
