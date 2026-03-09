from __future__ import annotations

from PIL import Image

from web_pagez_to_pdf.image_processing import (
    apply_edit_transform,
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
