from __future__ import annotations

from PIL import Image

from web_pagez_to_pdf.image_processing import (
    apply_edit_transform,
    compute_page_slices,
    content_points_per_pixel,
    normalize_content_sizing_mode,
    suggest_auto_vertical_border_crop_with_confidence,
    suggest_navigation_crop,
    suggest_navigation_crop_with_confidence,
)
from web_pagez_to_pdf.models import EditAdjustments, PrintLayout


def test_crop_vertical_band_keeps_full_height() -> None:
    image = Image.new("RGB", (120, 80), "white")
    edits = EditAdjustments()
    edits.set_operation(
        "crop_vertical_band",
        {"left": 20, "top": 10, "width": 60, "height": 20},
    )
    transformed = apply_edit_transform(
        image,
        PrintLayout(zoom_percent=100.0, rotate_degrees=0),
        edits,
    )

    assert transformed.width == 60
    assert transformed.height == 80


def test_navigation_crop_suggestion_is_bounded() -> None:
    image = Image.new("RGB", (400, 600), "white")
    left, right = suggest_navigation_crop(image)

    assert 0 <= left <= int(400 * 0.22)
    assert 0 <= right <= int(400 * 0.22)


def test_navigation_crop_confidence_on_blank_image_is_false() -> None:
    image = Image.new("RGB", (320, 480), "white")
    left, right, left_confident, right_confident = (
        suggest_navigation_crop_with_confidence(image)
    )

    assert left == 0
    assert right == 0
    assert not left_confident
    assert not right_confident


def test_auto_vertical_border_crop_detects_side_borders() -> None:
    image = Image.new("RGB", (320, 220), (230, 230, 230))
    for y_pos in range(image.height):
        for x_pos in range(34, 286):
            image.putpixel((x_pos, y_pos), (255, 255, 255))
        if y_pos % 6 == 0:
            for x_pos in range(70, 250, 14):
                image.putpixel((x_pos, y_pos), (30, 30, 30))

    left, right, left_ok, right_ok = suggest_auto_vertical_border_crop_with_confidence(
        image
    )

    assert left_ok and right_ok
    assert left >= 20
    assert right >= 20


def test_auto_vertical_border_crop_blank_image_is_noop() -> None:
    image = Image.new("RGB", (320, 240), "white")

    left, right, left_ok, right_ok = suggest_auto_vertical_border_crop_with_confidence(
        image
    )

    assert left == 0
    assert right == 0
    assert not left_ok
    assert not right_ok


def test_removed_wizard_operations_are_ignored_without_crash() -> None:
    image = Image.new("RGB", (180, 80), "white")
    edits = EditAdjustments()
    edits.set_operation("wizard_scrollbar_trim", {"right": 12})
    edits.set_operation(
        "wizard_border_trim",
        {"left": 5, "right": 7, "top": 3, "bottom": 4},
    )

    transformed = apply_edit_transform(
        image,
        PrintLayout(zoom_percent=100.0, rotate_degrees=0),
        edits,
    )

    assert transformed.width == 180
    assert transformed.height == 80


def test_legacy_nav_auto_crop_operation_still_applies() -> None:
    image = Image.new("RGB", (180, 120), "white")
    edits = EditAdjustments()
    edits.set_operation("nav_auto_crop", {"left": 12, "right": 18})

    transformed = apply_edit_transform(
        image,
        PrintLayout(zoom_percent=100.0, rotate_degrees=0),
        edits,
    )

    assert transformed.width == 150
    assert transformed.height == 120


def test_normalize_content_sizing_mode_defaults_to_legacy_fit_width() -> None:
    assert normalize_content_sizing_mode(None) == "legacy_fit_width"
    assert normalize_content_sizing_mode("unknown-mode") == "legacy_fit_width"
    assert normalize_content_sizing_mode("Fit_To_Page") == "fit_to_page"


def test_content_points_per_pixel_scales_by_mode() -> None:
    layout = PrintLayout()
    legacy_ppp = content_points_per_pixel(420, 2400, layout, "legacy_fit_width")
    fit_ppp = content_points_per_pixel(420, 2400, layout, "fit_to_page")
    original_ppp = content_points_per_pixel(420, 2400, layout, "original_size")
    stretch_large_ppp = content_points_per_pixel(
        420, 2400, layout, "stretch_if_smaller"
    )
    stretch_small_ppp = content_points_per_pixel(200, 120, layout, "stretch_if_smaller")
    original_small_ppp = content_points_per_pixel(200, 120, layout, "original_size")

    assert legacy_ppp > original_ppp
    assert fit_ppp < original_ppp
    assert abs(stretch_large_ppp - original_ppp) <= 0.0001
    assert stretch_small_ppp > original_small_ppp


def test_content_points_per_pixel_applies_scale_except_fit_to_page() -> None:
    layout = PrintLayout()
    legacy_default = content_points_per_pixel(420, 2400, layout, "legacy_fit_width")
    legacy_scaled = content_points_per_pixel(
        420,
        2400,
        layout,
        "legacy_fit_width",
        scale_percent=150.0,
    )
    fit_default = content_points_per_pixel(420, 2400, layout, "fit_to_page")
    fit_scaled = content_points_per_pixel(
        420,
        2400,
        layout,
        "fit_to_page",
        scale_percent=150.0,
    )

    assert legacy_scaled > legacy_default
    assert abs(fit_scaled - fit_default) <= 0.0001


def test_compute_page_slices_supports_sizing_modes() -> None:
    image = Image.new("RGB", (420, 2400), "white")
    layout = PrintLayout()

    slices_legacy_default = compute_page_slices(image, layout, [])
    slices_legacy_explicit = compute_page_slices(
        image,
        layout,
        [],
        content_sizing_mode="legacy_fit_width",
    )
    slices_fit = compute_page_slices(
        image,
        layout,
        [],
        content_sizing_mode="fit_to_page",
    )
    slices_original = compute_page_slices(
        image,
        layout,
        [],
        content_sizing_mode="original_size",
    )
    slices_stretch = compute_page_slices(
        image,
        layout,
        [],
        content_sizing_mode="stretch_if_smaller",
    )

    assert len(slices_legacy_default) == len(slices_legacy_explicit)
    assert len(slices_fit) == 1
    assert len(slices_original) > 1
    assert len(slices_stretch) == len(slices_original)


def test_compute_page_slices_fit_to_page_ignores_markers_and_scale() -> None:
    image = Image.new("RGB", (420, 2400), "white")
    layout = PrintLayout()

    slices = compute_page_slices(
        image,
        layout,
        [400, 1200, 1800],
        content_sizing_mode="fit_to_page",
        scale_percent=200.0,
    )

    assert len(slices) == 1
    assert slices[0].top == 0
    assert slices[0].bottom == 2400


def test_compute_page_slices_respects_manual_marker_with_scale() -> None:
    image = Image.new("RGB", (420, 2400), "white")
    layout = PrintLayout()

    slices = compute_page_slices(
        image,
        layout,
        [600],
        content_sizing_mode="legacy_fit_width",
        scale_percent=150.0,
    )

    assert any(slice_obj.bottom == 600 for slice_obj in slices)
