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
        if op_type == "crop_vertical_band":
            transformed = _apply_crop_vertical_band(transformed, params)
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
        if op_type in {"nav_auto_crop", "auto_vertical_border_crop"}:
            transformed = _apply_nav_crop(transformed, params)
            continue
        if op_type == "wizard_scrollbar_trim":
            transformed = _apply_wizard_scrollbar_trim(transformed, params)
            continue
        if op_type == "wizard_border_trim":
            transformed = _apply_wizard_border_trim(transformed, params)
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


def _apply_crop_vertical_band(image: Image.Image, params: dict[str, object]) -> Image.Image:
    left = max(0, int(params.get("left", 0)))
    width = max(1, int(params.get("width", image.width)))
    x1 = min(left, max(0, image.width - 1))
    x2 = min(image.width, x1 + width)
    if x2 <= x1:
        return image
    return image.crop((x1, 0, x2, image.height))


def _apply_nav_crop(image: Image.Image, params: dict[str, object]) -> Image.Image:
    left = max(0, int(params.get("left", 0)))
    right = max(0, int(params.get("right", 0)))
    x1 = min(left, max(0, image.width - 1))
    x2 = max(1, image.width - right)
    if x2 <= x1:
        return image
    return image.crop((x1, 0, x2, image.height))


def _apply_wizard_scrollbar_trim(image: Image.Image, params: dict[str, object]) -> Image.Image:
    right = max(0, int(params.get("right", 0)))
    x2 = max(1, image.width - right)
    if x2 <= 0:
        return image
    return image.crop((0, 0, x2, image.height))


def _apply_wizard_border_trim(image: Image.Image, params: dict[str, object]) -> Image.Image:
    left = max(0, int(params.get("left", 0)))
    right = max(0, int(params.get("right", 0)))
    top = max(0, int(params.get("top", 0)))
    bottom = max(0, int(params.get("bottom", 0)))
    x1 = min(left, max(0, image.width - 1))
    x2 = max(1, image.width - right)
    y1 = min(top, max(0, image.height - 1))
    y2 = max(1, image.height - bottom)
    if x2 <= x1 or y2 <= y1:
        return image
    return image.crop((x1, y1, x2, y2))


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
    """Legacy wrapper for center-out auto vertical border crop."""

    left_px, right_px, _left_confident, _right_confident = (
        suggest_auto_vertical_border_crop_with_confidence(image)
    )
    return (left_px, right_px)


def suggest_navigation_crop_with_confidence(
    image: Image.Image,
) -> tuple[int, int, bool, bool]:
    """Legacy wrapper preserving older API shape."""

    return suggest_auto_vertical_border_crop_with_confidence(image)


def suggest_auto_vertical_border_crop_with_confidence(
    image: Image.Image,
) -> tuple[int, int, bool, bool]:
    """Detect minimal left/right border crop by scanning each sampled row from a robust center."""

    gray = np.asarray(image.convert("L"), dtype=np.float32)
    if gray.ndim != 2:
        return (0, 0, False, False)
    height, width = gray.shape[:2]
    if width < 48 or height < 64:
        return (0, 0, False, False)

    sampled_rows = _sampled_row_indices(height)
    if sampled_rows.size == 0:
        return (0, 0, False, False)

    center_x = _robust_content_center(gray, sampled_rows)
    if center_x is None:
        return (0, 0, False, False)

    left_bounds: list[int] = []
    right_bounds: list[int] = []
    border_band = max(4, min(32, width // 12))
    for y_pos in sampled_rows:
        row = gray[int(y_pos), :]
        gradients = np.abs(np.diff(row, prepend=row[0]))
        left_ref = float(np.median(row[:border_band]))
        right_ref = float(np.median(row[width - border_band : width]))
        left_threshold = max(8.0, float(np.std(row[:border_band])) * 2.8 + 6.0)
        right_threshold = max(8.0, float(np.std(row[width - border_band : width])) * 2.8 + 6.0)
        gradient_threshold = max(6.0, float(np.percentile(gradients, 85.0)) * 1.1)

        left_edge = _scan_row_content_edge(
            row=row,
            gradients=gradients,
            start_x=center_x,
            stop_x=-1,
            step=-1,
            border_reference=left_ref,
            amplitude_threshold=left_threshold,
            gradient_threshold=gradient_threshold,
        )
        right_edge = _scan_row_content_edge(
            row=row,
            gradients=gradients,
            start_x=center_x,
            stop_x=width,
            step=1,
            border_reference=right_ref,
            amplitude_threshold=right_threshold,
            gradient_threshold=gradient_threshold,
        )
        if left_edge is not None:
            left_bounds.append(int(left_edge))
        if right_edge is not None:
            right_bounds.append(int(right_edge))

    minimum_rows = max(6, sampled_rows.size // 6)
    if len(left_bounds) < minimum_rows and len(right_bounds) < minimum_rows:
        return (0, 0, False, False)

    content_left = min(left_bounds) if left_bounds else 0
    content_right = max(right_bounds) if right_bounds else width - 1
    padding = max(2, min(12, int(round(width * 0.01))))
    content_left = max(0, content_left - padding)
    content_right = min(width - 1, content_right + padding)
    if content_right <= content_left:
        return (0, 0, False, False)

    left_crop = max(0, int(content_left))
    right_crop = max(0, int((width - 1) - content_right))
    if left_crop + right_crop >= width - 12:
        return (0, 0, False, False)

    left_confident = len(left_bounds) >= minimum_rows and left_crop > 0
    right_confident = len(right_bounds) >= minimum_rows and right_crop > 0
    if not left_confident and not right_confident:
        return (0, 0, False, False)
    return (left_crop, right_crop, bool(left_confident), bool(right_confident))


def suggest_scrollbar_trim_with_confidence(image: Image.Image) -> tuple[int, bool]:
    """Estimate right scrollbar trim with lightweight confidence checks."""

    gray = np.asarray(image.convert("L"), dtype=np.float32)
    height, width = gray.shape[:2]
    if width < 48 or height < 64:
        return (0, False)
    max_trim = min(180, max(0, int(width * 0.18)))
    if max_trim <= 0:
        return (0, False)
    band_start = max(0, width - max_trim - 2)
    band = gray[:, band_start:width]
    if band.size == 0:
        return (0, False)

    col_activity = np.mean(np.abs(np.diff(band, axis=0)), axis=0)
    stable_cols = 0
    for score in reversed(col_activity.tolist()):
        if float(score) <= 3.0:
            stable_cols += 1
            continue
        break
    if stable_cols < 4:
        return (0, False)
    trim = min(max_trim, stable_cols)
    boundary_x = width - trim
    if boundary_x <= 0 or boundary_x >= width:
        return (0, False)
    outer = gray[:, boundary_x:width]
    inner = gray[:, max(0, boundary_x - 4):boundary_x]
    if outer.size == 0 or inner.size == 0:
        return (0, False)
    boundary_contrast = float(np.mean(np.abs(gray[:, boundary_x - 1] - gray[:, boundary_x])))
    outer_std = float(np.std(outer))
    inner_std = float(np.std(inner))
    confident = boundary_contrast >= 1.8 and outer_std <= 46.0 and inner_std >= 6.0
    return (int(trim), bool(confident))


def suggest_window_border_trim_with_confidence(
    image: Image.Image,
) -> tuple[int, int, int, int, bool, bool, bool, bool]:
    """Peel likely window border from outside toward center with per-side confidence flags."""

    gray = np.asarray(image.convert("L"), dtype=np.float32)
    height, width = gray.shape[:2]
    if width < 60 or height < 60:
        return (0, 0, 0, 0, False, False, False, False)

    max_left = min(120, max(0, int(width * 0.18)))
    max_right = max_left
    max_top = min(120, max(0, int(height * 0.18)))
    max_bottom = max_top
    variance_threshold = 10.0
    contrast_threshold = 7.0

    left = _peel_border_side(
        gray,
        side="left",
        max_peel=max_left,
        variance_threshold=variance_threshold,
        contrast_threshold=contrast_threshold,
    )
    right = _peel_border_side(
        gray,
        side="right",
        max_peel=max_right,
        variance_threshold=variance_threshold,
        contrast_threshold=contrast_threshold,
    )
    top = _peel_border_side(
        gray,
        side="top",
        max_peel=max_top,
        variance_threshold=variance_threshold,
        contrast_threshold=contrast_threshold,
    )
    bottom = _peel_border_side(
        gray,
        side="bottom",
        max_peel=max_bottom,
        variance_threshold=variance_threshold,
        contrast_threshold=contrast_threshold,
    )

    max_keep_width = max(20, width - 40)
    max_keep_height = max(20, height - 40)
    if left + right >= max_keep_width:
        left = 0
        right = 0
    if top + bottom >= max_keep_height:
        top = 0
        bottom = 0
    return (
        int(left),
        int(right),
        int(top),
        int(bottom),
        bool(left > 0),
        bool(right > 0),
        bool(top > 0),
        bool(bottom > 0),
    )


def _detect_navigation_crop(image: Image.Image) -> tuple[int, int, bool, bool]:
    return suggest_auto_vertical_border_crop_with_confidence(image)


def _sampled_row_indices(height: int, *, max_rows: int = 96) -> np.ndarray:
    margin = max(2, min(24, height // 24))
    start = max(0, margin)
    stop = max(start + 1, height - margin)
    count = min(max_rows, max(1, stop - start))
    sampled = np.linspace(start, stop - 1, num=count, dtype=np.int32)
    return np.unique(sampled)


def _robust_content_center(gray: np.ndarray, sampled_rows: np.ndarray) -> int | None:
    width = int(gray.shape[1])
    border_band = max(4, min(32, width // 12))
    centers: list[float] = []
    for y_pos in sampled_rows:
        row = gray[int(y_pos), :]
        left_ref = float(np.median(row[:border_band]))
        right_ref = float(np.median(row[width - border_band : width]))
        baseline = (left_ref + right_ref) * 0.5
        deviation = np.abs(row - baseline)
        threshold = max(10.0, float(np.percentile(deviation, 85.0)) * 0.5)
        content = np.flatnonzero(deviation >= threshold)
        if content.size < max(3, width // 40):
            continue
        centers.append(float(np.median(content)))
    if not centers:
        return None
    center_x = int(round(float(np.median(np.asarray(centers, dtype=np.float32)))))
    return max(1, min(width - 2, center_x))


def _scan_row_content_edge(
    *,
    row: np.ndarray,
    gradients: np.ndarray,
    start_x: int,
    stop_x: int,
    step: int,
    border_reference: float,
    amplitude_threshold: float,
    gradient_threshold: float,
) -> int | None:
    if step == 0:
        return None
    border_run_required = 5
    last_content: int | None = None
    border_run = 0
    for x_pos in range(int(start_x), int(stop_x), int(step)):
        amplitude = abs(float(row[x_pos]) - float(border_reference))
        gradient = float(gradients[x_pos]) if 0 <= x_pos < int(gradients.shape[0]) else 0.0
        if amplitude >= float(amplitude_threshold) or gradient >= float(gradient_threshold):
            last_content = int(x_pos)
            border_run = 0
            continue
        if last_content is None:
            continue
        border_run += 1
        if border_run >= border_run_required:
            return int(last_content)
    return last_content


def _peel_border_side(
    gray: np.ndarray,
    *,
    side: str,
    max_peel: int,
    variance_threshold: float,
    contrast_threshold: float,
) -> int:
    height, width = gray.shape[:2]
    if side in {"left", "right"}:
        max_candidate = min(max_peel, width - 2)
    else:
        max_candidate = min(max_peel, height - 2)
    if max_candidate <= 0:
        return 0

    best = 0
    for peel in range(1, max_candidate + 1):
        if side == "left":
            border_strip = gray[:, :peel]
            outer_edge = gray[:, peel - 1]
            inner_edge = gray[:, peel]
        elif side == "right":
            border_strip = gray[:, width - peel : width]
            outer_edge = gray[:, width - peel]
            inner_edge = gray[:, width - peel - 1]
        elif side == "top":
            border_strip = gray[:peel, :]
            outer_edge = gray[peel - 1, :]
            inner_edge = gray[peel, :]
        else:
            border_strip = gray[height - peel : height, :]
            outer_edge = gray[height - peel, :]
            inner_edge = gray[height - peel - 1, :]

        variance = float(np.std(border_strip))
        contrast = float(np.mean(np.abs(outer_edge - inner_edge)))
        if variance <= variance_threshold and contrast >= contrast_threshold:
            best = peel
    return int(best)


def _column_edge_density(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 64, 180)
    density = edges.mean(axis=0).astype(np.float32)
    max_val = float(np.max(density)) if density.size else 0.0
    if max_val <= 0.0:
        return np.zeros_like(density, dtype=np.float32)
    return density / max_val


def _column_vertical_continuity(gray: np.ndarray) -> np.ndarray:
    gradient = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    strength = np.abs(gradient)
    kernel = np.ones((31, 1), dtype=np.float32) / 31.0
    smooth = cv2.filter2D(strength, -1, kernel, borderType=cv2.BORDER_REFLECT)
    continuity = smooth.mean(axis=0).astype(np.float32)
    max_val = float(np.max(continuity)) if continuity.size else 0.0
    if max_val <= 0.0:
        return np.zeros_like(continuity, dtype=np.float32)
    return continuity / max_val


def _pick_side_crop(
    score: np.ndarray,
    *,
    side: str,
    side_start: int,
    side_end: int,
    max_edge_crop: int,
    margin_px: int,
) -> tuple[int, bool]:
    side_slice = score[side_start:side_end]
    if side_slice.size == 0:
        return (0, False)
    threshold = max(float(np.percentile(side_slice, 75)), 0.11)
    candidates = np.where(side_slice >= threshold)[0]
    if candidates.size == 0:
        return (0, False)

    if side == "left":
        boundary = side_start + int(candidates[-1])
        crop = min(max_edge_crop, max(0, boundary + margin_px))
    else:
        boundary = side_start + int(candidates[0])
        crop = min(max_edge_crop, max(0, score.size - boundary + margin_px))
    return (int(crop), True)


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
