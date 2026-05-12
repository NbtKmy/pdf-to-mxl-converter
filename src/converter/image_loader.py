"""Render PDF pages to PNGs matching the dimensions Audiveris reported.

Audiveris records each sheet's binarised image dimensions in ``sheet#N.xml``
(``<picture width=W height=H>``). Zone bounding boxes in ``MeasureZone`` are
expressed in those exact pixel coordinates, so the PNG we expose to the
editor must be rendered at the same width/height — otherwise zone overlays
will be misaligned against the image.

Image inputs (PNG/JPG) are first wrapped into a single PDF by
``images_to_pdf`` so the downstream pipeline stays one code path.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .omr_parser import OmrData


_AUDIVERIS_RENDER_DPI = 300


def images_to_pdf(image_paths: list[Path], out_path: Path) -> Path:
    """Wrap a list of raster images into a single PDF, one image per page.

    Each page's point dimensions are set to ``pixels * 72 / 300`` so that
    when Audiveris renders the PDF (default ~300 DPI) the result matches
    the source pixel resolution — important because Audiveris rejects
    pages over 20 MP, and pymupdf's default 96-DPI page sizing inflates
    the render to ~3× source.

    The page order is the order of ``image_paths`` — caller is responsible
    for sorting (natural sort by filename for the multi-upload UI).
    """
    if not image_paths:
        raise ValueError("images_to_pdf requires at least one image path")
    doc = pymupdf.open()
    try:
        for img_path in image_paths:
            pix = pymupdf.Pixmap(str(img_path))
            w_pt = pix.width * 72.0 / _AUDIVERIS_RENDER_DPI
            h_pt = pix.height * 72.0 / _AUDIVERIS_RENDER_DPI
            page = doc.new_page(width=w_pt, height=h_pt)
            page.insert_image(page.rect, filename=str(img_path))
        doc.save(str(out_path))
    finally:
        doc.close()
    return out_path


def pdf_to_pngs(pdf_path: Path, omr_data: OmrData, output_dir: Path) -> dict[int, Path]:
    """Render each OMR sheet's underlying PDF page to a PNG.

    Returns a mapping of ``sheet_num`` → output PNG path. Sheets whose
    ``sheet_num`` exceeds the PDF page count are skipped.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[int, Path] = {}
    with pymupdf.open(str(pdf_path)) as doc:
        for sheet in omr_data.sheets:
            page_idx = sheet.sheet_num - 1
            if page_idx < 0 or page_idx >= doc.page_count:
                continue
            page = doc[page_idx]
            scale = sheet.width / page.rect.width if page.rect.width else 1.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
            out_path = output_dir / f"page-{sheet.sheet_num:03d}.png"
            pix.save(str(out_path))
            rendered[sheet.sheet_num] = out_path
    return rendered
