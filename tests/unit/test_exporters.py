from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image

from web_pagez_to_pdf.exporters import _meaningful_rich_text_or_empty, run_export
from web_pagez_to_pdf.models import (
    CaptureItem,
    EditAdjustments,
    ExportFormats,
    ExportRequest,
    PrintLayout,
)

if TYPE_CHECKING:
    from pathlib import Path


def _request_for_image(
    image_path: Path,
    output_dir: Path,
    *,
    basename: str,
    formats: ExportFormats,
) -> ExportRequest:
    item = CaptureItem(
        item_id="capture-1",
        title="sample",
        image_path=image_path,
        source_hwnd=None,
        frame_count=1,
    )
    return ExportRequest(
        captures=[item],
        combine_mode=False,
        formats=formats,
        output_dir=output_dir,
        basename=basename,
        docx_pptx_mode="per_split_page",
        layout=PrintLayout(),
        edits_by_item_id={item.item_id: EditAdjustments()},
    )


def test_run_export_paged_png_plus_tiff_succeeds(tmp_path: Path) -> None:
    image_path = tmp_path / "input.png"
    Image.new("RGB", (450, 1200), "white").save(image_path, format="PNG")
    request = _request_for_image(
        image_path=image_path,
        output_dir=tmp_path,
        basename="combo",
        formats=ExportFormats(pdf=False, paged_images=True, tiff=True),
    )

    result = run_export(request)

    suffixes = {path.suffix.lower() for path in result.generated_paths}
    assert ".tiff" in suffixes
    assert ".png" in suffixes
    assert (tmp_path / "combo.tiff").exists()


def test_tiff_export_falls_back_when_compressed_writer_raises_typeerror(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image_path = tmp_path / "input.png"
    Image.new("RGB", (420, 1050), "white").save(image_path, format="PNG")
    request = _request_for_image(
        image_path=image_path,
        output_dir=tmp_path,
        basename="fallback",
        formats=ExportFormats(pdf=False, paged_images=True, tiff=True),
    )
    original_save = Image.Image.save
    state = {"raised": False}

    def _patched_save(self, fp, format=None, **params):
        if (
            str(format or "").upper() == "TIFF"
            and str(params.get("compression", "")) == "tiff_deflate"
            and not state["raised"]
        ):
            state["raised"] = True
            raise TypeError("function takes exactly 7 arguments (11 given)")
        return original_save(self, fp, format=format, **params)

    monkeypatch.setattr(Image.Image, "save", _patched_save)

    result = run_export(request)

    assert state["raised"] is True
    assert (tmp_path / "fallback.tiff").exists()
    assert any(path.suffix.lower() == ".png" for path in result.generated_paths)


def test_run_export_respects_selected_formats_without_implicit_pdf(tmp_path: Path) -> None:
    image_path = tmp_path / "input.png"
    Image.new("RGB", (320, 900), "white").save(image_path, format="PNG")
    request = _request_for_image(
        image_path=image_path,
        output_dir=tmp_path,
        basename="long-only",
        formats=ExportFormats(pdf=False, long_image=True),
    )

    result = run_export(request)

    assert len(result.generated_paths) == 1
    assert result.generated_paths[0].name == "long-only_long.png"


def test_meaningful_rich_text_filter_treats_css_only_html_as_blank() -> None:
    css_only_html = """
    <html><head><style type="text/css">
    p, li { white-space: pre-wrap; }
    hr { height: 1px; border-width: 0; }
    li.unchecked::marker { content: "\\2610"; }
    li.checked::marker { content: "\\2612"; }
    </style></head><body></body></html>
    """
    assert _meaningful_rich_text_or_empty(css_only_html) == ""
    assert _meaningful_rich_text_or_empty("<p>Header</p>").strip() != ""
