"""Image transforms, crop analysis, and page splitting utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image, ImageQt
from reportlab.lib.pagesizes import A0, A1, A2, A3, A4, A5, A6, LEGAL, LETTER, TABLOID
from reportlab.lib.units import mm

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .models import EditAdjustments, PrintLayout

PAPER_SIZES: dict[str, tuple[float, float]] = {
    "A0": A0,
    "A1": A1,
    "A2": A2,
    "A3": A3,
    "A4": A4,
    "A5": A5,
    "A6": A6,
    "LETTER": LETTER,
    "LEGAL": LEGAL,
    "TABLOID": TABLOID,
}


@dataclass(slots=True)
class PageSlice:
    """Vertical pixel segment corresponding to one rendered page frame."""

    top: int
    bottom: int


def pil_to_qpixmap(image: Image.Image):
    """Convert PIL image to Qt pixmap."""

    return ImageQt.toqpixmap(image)


def apply_edit_transform(image: Image.Image, layout: PrintLayout, edits: EditAdjustments) -> Image.Image:
    """Apply user edits and print transform knobs to a source image."""

    transformed = image.convert("RGB")

    rotate = int(layout.rotate_degrees) % 360
    if rotate:
        transformed = transformed.rotate(-rotate, expand=True, fillcolor="white")

    left = max(0, int(edits.crop_left_px + edits.auto_crop_left_px))
    right = max(0, int(edits.crop_right_px + edits.auto_crop_right_px))
    top = max(0, int(edits.crop_top_px))
    bottom = max(0, int(edits.crop_bottom_px))

    width = transformed.width
    height = transformed.height
    crop_box = (
        min(left, width - 1),
        min(top, height - 1),
        max(1, width - right),
        max(1, height - bottom),
    )
    if crop_box[2] <= crop_box[0]:
        crop_box = (0, crop_box[1], width, crop_box[3])
    if crop_box[3] <= crop_box[1]:
        crop_box = (crop_box[0], 0, crop_box[2], height)
    transformed = transformed.crop(crop_box)

    zoom = max(10.0, float(layout.zoom_percent))
    if zoom != 100.0:
        factor = zoom / 100.0
        new_size = (
            max(1, round(transformed.width * factor)),
            max(1, round(transformed.height * factor)),
        )
        transformed = transformed.resize(new_size, Image.Resampling.LANCZOS)
    return transformed


def suggest_navigation_crop(image: Image.Image) -> tuple[int, int]:
    """Heuristic suggestion for left/right crop to remove side navigation."""

    gray = np.asarray(image.convert("L"))
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 64, 180)
    density = edges.mean(axis=0)
    smooth = cv2.GaussianBlur(density.astype(np.float32), (1, 21), 0).reshape(-1)

    if smooth.size <= 10:
        return (0, 0)

    nonzero = smooth[smooth > 0.0]
    if nonzero.size == 0:
        return (0, 0)
    threshold = float(np.percentile(nonzero, 40))

    left = 0
    for idx in range(min(smooth.size // 3, smooth.size)):
        if smooth[idx] >= threshold:
            left = max(0, idx - 6)
            break

    right_crop = 0
    for reverse_idx in range(smooth.size - 1, max((2 * smooth.size) // 3, 0), -1):
        if smooth[reverse_idx] >= threshold:
            right_crop = max(0, smooth.size - reverse_idx - 6)
            break

    max_edge_crop = int(image.width * 0.18)
    return (min(left, max_edge_crop), min(right_crop, max_edge_crop))


def compute_page_slices(image: Image.Image, layout: PrintLayout, manual_markers: Iterable[int]) -> list[PageSlice]:
    """Compute vertical split points matching printable page height."""

    page_size = PAPER_SIZES.get(layout.paper_name.upper(), A4)
    page_w, page_h = page_size
    if layout.orientation.lower() == "landscape":
        page_w, page_h = page_h, page_w

    avail_w = page_w - (layout.margin_left_mm + layout.margin_right_mm + layout.gutter_mm) * mm
    avail_h = page_h - (layout.margin_top_mm + layout.margin_bottom_mm) * mm

    if image.width <= 0:
        return [PageSlice(0, image.height)]
    scale = avail_w / float(image.width)
    max_slice_px = max(24, int(avail_h / scale))

    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    row_mins = gray.min(axis=1)
    blank_rows = row_mins >= np.uint8(np.clip(layout.blank_row_threshold, 0, 255))
    markers = sorted({int(m) for m in manual_markers if 0 < int(m) < image.height})

    slices: list[PageSlice] = []
    top = 0
    marker_idx = 0
    while top < image.height:
        ideal_bottom = min(top + max_slice_px, image.height)
        if marker_idx < len(markers):
            next_marker = markers[marker_idx]
            if top < next_marker <= ideal_bottom:
                slices.append(PageSlice(top=top, bottom=next_marker))
                top = next_marker
                marker_idx += 1
                continue
        if ideal_bottom >= image.height:
            slices.append(PageSlice(top=top, bottom=image.height))
            break
        cut = find_best_cut(blank_rows, ideal_bottom, max(40, int(layout.search_window_px)))
        if cut <= top:
            cut = ideal_bottom
        slices.append(PageSlice(top=top, bottom=cut))
        top = cut
    return slices


def find_best_cut(blank_rows: np.ndarray, ideal_px: int, search_window: int) -> int:
    """Find blank-row cut near ideal point with backward search fallback."""

    search_start = max(0, ideal_px - search_window)
    safe_ideal = min(max(0, ideal_px), int(blank_rows.shape[0]) - 1)
    for row in range(safe_ideal, search_start - 1, -1):
        if bool(blank_rows[row]):
            return row
    return safe_ideal
