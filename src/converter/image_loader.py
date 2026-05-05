"""Render PDF pages to PNGs matching the dimensions Audiveris reported.

Audiveris records each sheet's binarised image dimensions in ``sheet#N.xml``
(``<picture width=W height=H>``). Zone bounding boxes in ``MeasureZone`` are
expressed in those exact pixel coordinates, so the PNG we expose to the
editor must be rendered at the same width/height — otherwise zone overlays
will be misaligned against the image.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .omr_parser import OmrData


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
