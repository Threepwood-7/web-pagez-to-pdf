from __future__ import annotations

from PIL import Image

from web_pagez_to_pdf.image_processing import (
    apply_edit_transform,
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
    left, right, left_confident, right_confident = suggest_navigation_crop_with_confidence(image)

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

    left, right, left_ok, right_ok = suggest_auto_vertical_border_crop_with_confidence(image)

    assert left_ok and right_ok
    assert left >= 20
    assert right >= 20


def test_auto_vertical_border_crop_blank_image_is_noop() -> None:
    image = Image.new("RGB", (320, 240), "white")

    left, right, left_ok, right_ok = suggest_auto_vertical_border_crop_with_confidence(image)

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
