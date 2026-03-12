from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

from PIL import Image
from reportlab.lib.units import mm

from web_pagez_to_pdf import exporters
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


def test_run_export_respects_selected_formats_without_implicit_pdf(
    tmp_path: Path,
) -> None:
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


def test_run_export_xlsx_only_writes_sheet_and_embedded_image(tmp_path: Path) -> None:
    image_path = tmp_path / "input.png"
    Image.new("RGB", (720, 500), "white").save(image_path, format="PNG")
    request = _request_for_image(
        image_path=image_path,
        output_dir=tmp_path,
        basename="xlsx-only",
        formats=ExportFormats(pdf=False, xlsx=True),
    )

    result = run_export(request)

    assert len(result.generated_paths) == 1
    output = tmp_path / "xlsx-only.xlsx"
    assert result.generated_paths[0] == output
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "xl/worksheets/sheet1.xml" in names
    assert any(name.startswith("xl/media/") for name in names)


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


def test_export_pdf_uses_left_margin_plus_gutter_for_content_origin(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request_for_image(
        image_path=tmp_path / "input.png",
        output_dir=tmp_path,
        basename="pdf-gutter-left",
        formats=ExportFormats(pdf=True),
    )
    Image.new("RGB", (320, 240), "white").save(
        request.captures[0].image_path, format="PNG"
    )
    request.layout = PrintLayout(
        paper_name="A4",
        orientation="portrait",
        margin_left_mm=15.0,
        margin_right_mm=12.0,
        margin_top_mm=20.0,
        margin_bottom_mm=20.0,
        gutter_mm=8.0,
        header_rich_text="<p>{title}</p>",
        footer_rich_text="<p>{page}/{pages}</p>",
    )

    class _FakeCanvas:
        def __init__(self, _path: str, *, pagesize: tuple[float, float]) -> None:
            self.page_w = float(pagesize[0])
            self.draw_calls: list[tuple[float, float, float, float]] = []

        def drawInlineImage(  # noqa: N802
            self, _image, x_pos: float, y_pos: float, *, width: float, height: float
        ) -> None:
            self.draw_calls.append(
                (float(x_pos), float(y_pos), float(width), float(height))
            )

        def showPage(self) -> None:  # noqa: N802
            return

        def setPageSize(self, _size: tuple[float, float]) -> None:  # noqa: N802
            return

        def save(self) -> None:
            return

    fake_canvas: _FakeCanvas | None = None
    rich_text_x_positions: list[float] = []

    def _canvas_factory(path: str, pagesize: tuple[float, float]):
        del path
        nonlocal fake_canvas
        fake_canvas = _FakeCanvas("", pagesize=pagesize)
        return fake_canvas

    def _fake_draw_rich_text(
        pdf_obj,
        rich_text: str,
        context: dict[str, str],
        x_pos: float,
        y_pos: float,
        width: float,
    ) -> None:
        del pdf_obj, rich_text, context, y_pos, width
        rich_text_x_positions.append(float(x_pos))

    monkeypatch.setattr(exporters.canvas, "Canvas", _canvas_factory)
    monkeypatch.setattr(exporters, "_draw_rich_text", _fake_draw_rich_text)

    frame = exporters.PageFrame(
        image=Image.new("RGB", (200, 120), "white"),
        title="sample",
        source_item=request.captures[0],
        page_index_in_item=1,
        page_count_in_item=1,
    )
    exporters.export_pdf(request, [frame])

    assert fake_canvas is not None
    assert len(fake_canvas.draw_calls) == 1
    x_pos, _y_pos, width, _height = fake_canvas.draw_calls[0]
    expected_x = (request.layout.margin_left_mm + request.layout.gutter_mm) * mm
    expected_width = (
        fake_canvas.page_w
        - (
            request.layout.margin_left_mm
            + request.layout.margin_right_mm
            + request.layout.gutter_mm
        )
        * mm
    )
    assert abs(x_pos - expected_x) <= 0.001
    assert abs(width - expected_width) <= 0.001
    assert rich_text_x_positions
    assert all(abs(pos - expected_x) <= 0.001 for pos in rich_text_x_positions)


def test_build_page_frames_preserves_manual_split_marker_with_scale(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "input.png"
    Image.new("RGB", (420, 2400), "white").save(image_path, format="PNG")
    request = _request_for_image(
        image_path=image_path,
        output_dir=tmp_path,
        basename="marker-scale",
        formats=ExportFormats(pdf=False, paged_images=True),
    )
    item = request.captures[0]
    edits = request.edits_by_item_id[item.item_id]
    edits.split_markers_px = [600]
    edits.set_operation("scale", {"percent": 180})

    frames, _transformed = exporters.build_page_frames(request)
    cumulative_bottom = 0
    boundaries: list[int] = []
    for frame in frames:
        cumulative_bottom += int(frame.image.height)
        boundaries.append(cumulative_bottom)

    assert any(int(boundary) == 600 for boundary in boundaries)
