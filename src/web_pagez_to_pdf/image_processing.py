"""Image transforms, crop analysis, and page splitting utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from PIL import Image, ImageDraw, ImageQt
from PySide6.QtGui import QPixmap
from reportlab.lib.pagesizes import A0, A1, A2, A3, A4, A5, A6, LEGAL, LETTER, TABLOID
from reportlab.lib.units import mm

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

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
DEFAULT_CONTENT_SIZING_MODE = "legacy_fit_width"
CONTENT_SIZING_MODES = {
    "legacy_fit_width",
    "fit_to_page",
    "stretch_if_smaller",
    "original_size",
}
NATIVE_PIXELS_PER_INCH = 96.0
POINTS_PER_INCH = 72.0
NATIVE_POINTS_PER_PIXEL = POINTS_PER_INCH / NATIVE_PIXELS_PER_INCH


@dataclass(slots=True)
class PageSlice:
    """Vertical pixel segment corresponding to one rendered page frame."""

    top: int
    bottom: int


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    """Convert PIL image to Qt pixmap."""

    image_qt: Any = ImageQt
    pixmap = image_qt.toqpixmap(image)
    if isinstance(pixmap, QPixmap):
        return pixmap
    raise TypeError("ImageQt.toqpixmap did not return a QPixmap")


def normalize_content_sizing_mode(mode: object) -> str:
    """Return a supported content sizing mode value."""

    normalized = str(mode or "").strip().lower()
    if normalized in CONTENT_SIZING_MODES:
        return normalized
    return DEFAULT_CONTENT_SIZING_MODE


def printable_content_area_points(layout: PrintLayout) -> tuple[float, float]:
    """Return printable content width/height in PDF points."""

    page_size = PAPER_SIZES.get(layout.paper_name.upper(), A4)
    page_w, page_h = page_size
    if layout.orientation.lower() == "landscape":
        page_w, page_h = page_h, page_w
    avail_w = (
        page_w
        - (layout.margin_left_mm + layout.margin_right_mm + layout.gutter_mm) * mm
    )
    avail_h = page_h - (layout.margin_top_mm + layout.margin_bottom_mm) * mm
    return (max(1.0, float(avail_w)), max(1.0, float(avail_h)))


def content_points_per_pixel(
    image_width_px: int,
    image_height_px: int,
    layout: PrintLayout,
    content_sizing_mode: str,
    *,
    scale_percent: float = 100.0,
) -> float:
    """Return rendered points-per-source-pixel for content sizing mode."""

    width_px = max(1, int(image_width_px))
    height_px = max(1, int(image_height_px))
    avail_w, avail_h = printable_content_area_points(layout)
    mode = normalize_content_sizing_mode(content_sizing_mode)
    scale_factor = max(0.1, float(scale_percent) / 100.0)
    if mode == DEFAULT_CONTENT_SIZING_MODE:
        base = max(0.0001, float(avail_w) / float(width_px))
        return max(0.0001, float(base) * float(scale_factor))

    natural_w = float(width_px) * NATIVE_POINTS_PER_PIXEL
    natural_h = float(height_px) * NATIVE_POINTS_PER_PIXEL
    fit_scale = min(float(avail_w) / natural_w, float(avail_h) / natural_h)

    if mode == "fit_to_page":
        mode_scale = max(0.0001, float(fit_scale))
        return max(0.0001, float(NATIVE_POINTS_PER_PIXEL) * float(mode_scale))
    elif mode == "stretch_if_smaller":
        if natural_w < float(avail_w) and natural_h < float(avail_h):
            mode_scale = max(0.0001, float(fit_scale))
        else:
            mode_scale = 1.0
    else:
        mode_scale = 1.0
    base = max(0.0001, float(NATIVE_POINTS_PER_PIXEL) * float(mode_scale))
    return max(0.0001, float(base) * float(scale_factor))


def apply_edit_transform(
    image: Image.Image,
    layout: PrintLayout,
    edits: EditAdjustments,
    *,
    include_scale: bool = True,
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
            if not include_scale:
                continue
            transformed = _apply_scale(transformed, params)
            continue
        if op_type in {"nav_auto_crop", "auto_vertical_border_crop"}:
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
                "height": max(
                    1,
                    transformed.height - int(edits.crop_top_px + edits.crop_bottom_px),
                ),
            },
        )

    transformed = _apply_rotation(transformed, {"degrees": layout.rotate_degrees})
    if include_scale:
        transformed = _apply_scale(transformed, {"percent": layout.zoom_percent})
    return transformed


def _apply_rotation(image: Image.Image, params: dict[str, object]) -> Image.Image:
    degrees = _coerce_float(params.get("degrees"), default=0.0)
    if abs(degrees) <= 0.01:
        return image
    return image.rotate(-degrees, expand=True, fillcolor="white")


def _apply_scale(image: Image.Image, params: dict[str, object]) -> Image.Image:
    percent = max(10.0, _coerce_float(params.get("percent"), default=100.0))
    if abs(percent - 100.0) <= 0.01:
        return image
    factor = percent / 100.0
    size = (
        max(1, round(image.width * factor)),
        max(1, round(image.height * factor)),
    )
    return image.resize(size, Image.Resampling.LANCZOS)


def _apply_crop_rect(image: Image.Image, params: dict[str, object]) -> Image.Image:
    left = max(0, _coerce_int(params.get("left"), default=0))
    top = max(0, _coerce_int(params.get("top"), default=0))
    width = max(1, _coerce_int(params.get("width"), default=image.width))
    height = max(1, _coerce_int(params.get("height"), default=image.height))

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
    point_values = _object_sequence(params.get("points"))
    if point_values is None or len(point_values) < 2:
        return image

    xs: list[int] = []
    ys: list[int] = []
    for point in point_values:
        point_pair = _coerce_point_pair(point)
        if point_pair is None:
            continue
        xs.append(point_pair[0])
        ys.append(point_pair[1])
    if len(xs) < 2 or len(ys) < 2:
        return image
    left = max(0, min(xs))
    top = max(0, min(ys))
    right = min(image.width, max(xs))
    bottom = min(image.height, max(ys))
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))


def _apply_crop_vertical_band(
    image: Image.Image, params: dict[str, object]
) -> Image.Image:
    left = max(0, _coerce_int(params.get("left"), default=0))
    width = max(1, _coerce_int(params.get("width"), default=image.width))
    x1 = min(left, max(0, image.width - 1))
    x2 = min(image.width, x1 + width)
    if x2 <= x1:
        return image
    return image.crop((x1, 0, x2, image.height))


def _apply_nav_crop(image: Image.Image, params: dict[str, object]) -> Image.Image:
    left = max(0, _coerce_int(params.get("left"), default=0))
    right = max(0, _coerce_int(params.get("right"), default=0))
    x1 = min(left, max(0, image.width - 1))
    x2 = max(1, image.width - right)
    if x2 <= x1:
        return image
    return image.crop((x1, 0, x2, image.height))


def _apply_redactions(image: Image.Image, params: dict[str, object]) -> Image.Image:
    rectangles = _object_sequence(params.get("rectangles"))
    if rectangles is None or not rectangles:
        return image
    redacted = image.copy()
    draw = ImageDraw.Draw(redacted)
    for rect in rectangles:
        rect_values = _string_object_mapping(rect)
        if rect_values is None:
            continue
        x_pos = _coerce_int(rect_values.get("x"), default=0)
        y_pos = _coerce_int(rect_values.get("y"), default=0)
        width = max(1, _coerce_int(rect_values.get("width"), default=1))
        height = max(1, _coerce_int(rect_values.get("height"), default=1))
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
    """Detect left and right border crops around a robust sampled center."""

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
        right_threshold = max(
            8.0, float(np.std(row[width - border_band : width])) * 2.8 + 6.0
        )
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
    padding = max(2, min(12, round(width * 0.01)))
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
    center_x = round(float(np.median(np.asarray(centers, dtype=np.float32))))
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
        gradient = (
            float(gradients[x_pos]) if 0 <= x_pos < int(gradients.shape[0]) else 0.0
        )
        if amplitude >= float(amplitude_threshold) or gradient >= float(
            gradient_threshold
        ):
            last_content = int(x_pos)
            border_run = 0
            continue
        if last_content is None:
            continue
        border_run += 1
        if border_run >= border_run_required:
            return int(last_content)
    return last_content


def compute_page_slices(
    image: Image.Image,
    layout: PrintLayout,
    manual_markers: Iterable[int],
    *,
    content_sizing_mode: str = DEFAULT_CONTENT_SIZING_MODE,
    scale_percent: float = 100.0,
) -> list[PageSlice]:
    """Compute vertical split points matching printable page height."""

    mode = normalize_content_sizing_mode(content_sizing_mode)
    image_height = max(0, int(image.height))
    if image_height <= 0:
        return [PageSlice(0, image_height)]
    if mode == "fit_to_page":
        return [PageSlice(0, image_height)]

    _avail_w, avail_h = printable_content_area_points(layout)

    if image.width <= 0:
        return [PageSlice(0, image_height)]
    points_per_px = content_points_per_pixel(
        image.width,
        image_height,
        layout,
        mode,
        scale_percent=scale_percent,
    )
    max_slice_px = max(24, int(float(avail_h) / max(0.0001, float(points_per_px))))

    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    row_mins = gray.min(axis=1)
    blank_rows = row_mins >= np.uint8(np.clip(layout.blank_row_threshold, 0, 255))
    markers = sorted({int(m) for m in manual_markers if 0 < int(m) < image_height})

    slices: list[PageSlice] = []
    top = 0
    marker_idx = 0
    while top < image_height:
        ideal_bottom = min(top + max_slice_px, image_height)
        if marker_idx < len(markers):
            next_marker = markers[marker_idx]
            if top < next_marker <= ideal_bottom:
                slices.append(PageSlice(top=top, bottom=next_marker))
                top = next_marker
                marker_idx += 1
                continue
        if ideal_bottom >= image_height:
            slices.append(PageSlice(top=top, bottom=image_height))
            break
        cut = find_best_cut(
            blank_rows, ideal_bottom, max(40, int(layout.search_window_px))
        )
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


def _coerce_int(value: object, *, default: int) -> int:
    """Coerce loose JSON-like payload values into ints for edit operations."""

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


def _coerce_float(value: object, *, default: float) -> float:
    """Coerce loose JSON-like payload values into floats for edit operations."""

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


def _coerce_point_pair(value: object) -> tuple[int, int] | None:
    """Normalize a free-crop point payload into integer X/Y coordinates."""

    point_values = _object_sequence(value)
    if point_values is None or len(point_values) < 2:
        return None
    return (
        _coerce_int(point_values[0], default=0),
        _coerce_int(point_values[1], default=0),
    )


def _object_sequence(value: object) -> Sequence[object] | None:
    """Return list/tuple payloads as a typed object sequence."""

    if isinstance(value, (list, tuple)):
        return list(cast("Sequence[object]", value))
    return None


def _string_object_mapping(value: object) -> Mapping[str, object] | None:
    """Normalize ad-hoc dict payloads to string-key object mappings."""

    if not isinstance(value, dict):
        return None
    normalized: dict[str, object] = {}
    for key, item in cast("Mapping[object, object]", value).items():
        normalized[str(key)] = item
    return normalized
