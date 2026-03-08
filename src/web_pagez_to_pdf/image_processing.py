"""Image transforms, crop analysis, and page splitting utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageQt
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


def apply_edit_transform(
    image: Image.Image, layout: PrintLayout, edits: EditAdjustments
) -> Image.Image:
    """Apply non-destructive editor operations and print transform knobs."""

    transformed = image.convert("RGB")

    for operation in edits.operations:
        op_type = operation.op_type.strip().lower()
        params = operation.params

        if op_type == "crop_rect":
            transformed = _apply_crop_rect(transformed, params)
            continue
        if op_type == "crop_free":
            transformed = _apply_crop_free(transformed, params)
            continue
        if op_type in {"rotate", "straighten"}:
            transformed = _apply_rotation(transformed, params)
            continue
        if op_type == "scale":
            transformed = _apply_scale(transformed, params)
            continue
        if op_type == "nav_auto_crop":
            transformed = _apply_nav_crop(transformed, params)
            continue
        if op_type == "redact_rects":
            transformed = _apply_redactions(transformed, params)
            continue

    if any(
        (
            edits.crop_left_px,
            edits.crop_right_px,
            edits.crop_top_px,
            edits.crop_bottom_px,
            edits.auto_crop_left_px,
            edits.auto_crop_right_px,
        )
    ):
        transformed = _apply_nav_crop(
            transformed,
            {
                "left": int(edits.crop_left_px + edits.auto_crop_left_px),
                "right": int(edits.crop_right_px + edits.auto_crop_right_px),
            },
        )
        transformed = _apply_crop_rect(
            transformed,
            {
                "left": 0,
                "top": int(edits.crop_top_px),
                "width": transformed.width,
                "height": max(1, transformed.height - int(edits.crop_top_px + edits.crop_bottom_px)),
            },
        )

    transformed = _apply_rotation(transformed, {"degrees": layout.rotate_degrees})
    transformed = _apply_scale(transformed, {"percent": layout.zoom_percent})
    return transformed


def _apply_rotation(image: Image.Image, params: dict[str, object]) -> Image.Image:
    degrees = float(params.get("degrees", 0.0))
    if abs(degrees) <= 0.01:
        return image
    return image.rotate(-degrees, expand=True, fillcolor="white")


def _apply_scale(image: Image.Image, params: dict[str, object]) -> Image.Image:
    percent = max(10.0, float(params.get("percent", 100.0)))
    if abs(percent - 100.0) <= 0.01:
        return image
    factor = percent / 100.0
    size = (
        max(1, round(image.width * factor)),
        max(1, round(image.height * factor)),
    )
    return image.resize(size, Image.Resampling.LANCZOS)


def _apply_crop_rect(image: Image.Image, params: dict[str, object]) -> Image.Image:
    left = max(0, int(params.get("left", 0)))
    top = max(0, int(params.get("top", 0)))
    width = max(1, int(params.get("width", image.width)))
    height = max(1, int(params.get("height", image.height)))

    x1 = min(left, max(0, image.width - 1))
    y1 = min(top, max(0, image.height - 1))
    x2 = min(image.width, x1 + width)
    y2 = min(image.height, y1 + height)
    if x2 <= x1:
        x1, x2 = 0, image.width
    if y2 <= y1:
        y1, y2 = 0, image.height
    return image.crop((x1, y1, x2, y2))


def _apply_crop_free(image: Image.Image, params: dict[str, object]) -> Image.Image:
    points = params.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return image

    xs: list[int] = []
    ys: list[int] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        xs.append(int(point[0]))
        ys.append(int(point[1]))
    if len(xs) < 2 or len(ys) < 2:
        return image
    left = max(0, min(xs))
    top = max(0, min(ys))
    right = min(image.width, max(xs))
    bottom = min(image.height, max(ys))
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))


def _apply_nav_crop(image: Image.Image, params: dict[str, object]) -> Image.Image:
    left = max(0, int(params.get("left", 0)))
    right = max(0, int(params.get("right", 0)))
    x1 = min(left, max(0, image.width - 1))
    x2 = max(1, image.width - right)
    if x2 <= x1:
        return image
    return image.crop((x1, 0, x2, image.height))


def _apply_redactions(image: Image.Image, params: dict[str, object]) -> Image.Image:
    rectangles = params.get("rectangles")
    if not isinstance(rectangles, list) or not rectangles:
        return image
    redacted = image.copy()
    draw = ImageDraw.Draw(redacted)
    for rect in rectangles:
        if not isinstance(rect, dict):
            continue
        x_pos = int(rect.get("x", 0))
        y_pos = int(rect.get("y", 0))
        width = max(1, int(rect.get("width", 1)))
        height = max(1, int(rect.get("height", 1)))
        x1 = max(0, min(image.width - 1, x_pos))
        y1 = max(0, min(image.height - 1, y_pos))
        x2 = max(1, min(image.width, x1 + width))
        y2 = max(1, min(image.height, y1 + height))
        draw.rectangle((x1, y1, x2, y2), fill="black")
    return redacted


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


def compute_page_slices(
    image: Image.Image, layout: PrintLayout, manual_markers: Iterable[int]
) -> list[PageSlice]:
    """Compute vertical split points matching printable page height."""

    page_size = PAPER_SIZES.get(layout.paper_name.upper(), A4)
    page_w, page_h = page_size
    if layout.orientation.lower() == "landscape":
        page_w, page_h = page_h, page_w

    avail_w = page_w - (
        layout.margin_left_mm + layout.margin_right_mm + layout.gutter_mm
    ) * mm
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
