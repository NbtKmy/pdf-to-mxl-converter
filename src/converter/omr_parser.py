"""Parse Audiveris ``.omr`` project files to extract per-measure bounding boxes.

The ``.omr`` is a zip archive with this layout::

    book.xml                       — book-level metadata, sheet ordering
    sheet#1/sheet#1.xml            — sheet 1 OMR data
    sheet#1/BINARY.png             — binarised sheet 1 image
    sheet#2/sheet#2.xml
    sheet#2/BINARY.png
    ...

Inside ``sheet#N.xml``:
    <sheet>
      <picture width="W" height="H">...</picture>
      <page id="N" measure-count="K">
        <system id="1">
          <stack id="0" left="X1" right="X2" .../>   <!-- measure-stack X range -->
          <stack id="1" .../>
          ...
          <part id="1">
            <staff id="1" left="..." right="...">
              <lines>
                <line ...>
                  <point x="..." y="..."/>           <!-- staff line endpoints -->
                  <point x="..." y="..."/>
                </line>
                ...
              </lines>
            </staff>
            <staff id="2">...</staff>
          </part>
        </system>
        <system id="2">...</system>
        ...
      </page>
    </sheet>

A "measure" in MEI corresponds to one ``<stack>``: it spans all staves of all
parts in the containing ``<system>``. Audiveris does not store a full ``(ulx,
uly, lrx, lry)`` rectangle on the stack itself, so we synthesise the vertical
extent from the staff line points within the same system.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from lxml import etree


@dataclass
class MeasureZone:
    """Bounding box of a single measure (= one Audiveris stack) on its sheet."""

    sheet_num: int
    system_index: int   # 1-origin, position of the system within the page
    stack_id: int       # Audiveris ``<stack id>``; 0-origin within the sheet
    ulx: int
    uly: int
    lrx: int
    lry: int


@dataclass
class SheetInfo:
    sheet_num: int
    width: int
    height: int
    binary_image_path: str  # path *inside* the .omr zip (e.g. "sheet#1/BINARY.png")
    measure_zones: list[MeasureZone] = field(default_factory=list)


@dataclass
class OmrData:
    sheets: list[SheetInfo]

    @property
    def total_measures(self) -> int:
        return sum(len(s.measure_zones) for s in self.sheets)


def parse_omr(omr_path: Path | str) -> OmrData:
    """Parse a ``.omr`` file and return per-sheet measure bounding boxes."""
    omr_path = Path(omr_path)
    with zipfile.ZipFile(omr_path, "r") as zf:
        names = zf.namelist()
        sheet_xml_names = sorted(n for n in names if n.endswith(".xml") and n.startswith("sheet#"))
        sheets: list[SheetInfo] = []
        for xml_name in sheet_xml_names:
            sheet_dir = xml_name.split("/", 1)[0]  # "sheet#1"
            sheet_num = _sheet_number_from_dir(sheet_dir)
            with zf.open(xml_name) as f:
                tree = etree.parse(f)
            sheets.append(_parse_sheet(tree, sheet_num, f"{sheet_dir}/BINARY.png"))
    return OmrData(sheets=sheets)


def _sheet_number_from_dir(name: str) -> int:
    # "sheet#1" -> 1
    return int(name.removeprefix("sheet#"))


def _parse_sheet(tree: etree._ElementTree, sheet_num: int, binary_image_path: str) -> SheetInfo:
    root = tree.getroot()
    picture = root.find("picture")
    width = int(picture.get("width"))
    height = int(picture.get("height"))

    sheet = SheetInfo(
        sheet_num=sheet_num,
        width=width,
        height=height,
        binary_image_path=binary_image_path,
    )

    page = root.find("page")
    if page is None:
        return sheet

    by_stack_id: dict[int, MeasureZone] = {}
    for system_idx, system in enumerate(page.findall("system"), start=1):
        y_min, y_max = _system_y_extent(system)
        if y_min is None or y_max is None:
            continue
        for stack in system.findall("stack"):
            try:
                stack_id = int(stack.get("id"))
                ulx = int(stack.get("left"))
                lrx = int(stack.get("right"))
            except (TypeError, ValueError):
                continue
            existing = by_stack_id.get(stack_id)
            if existing is None:
                by_stack_id[stack_id] = MeasureZone(
                    sheet_num=sheet_num,
                    system_index=system_idx,
                    stack_id=stack_id,
                    ulx=ulx,
                    uly=y_min,
                    lrx=lrx,
                    lry=y_max,
                )
            else:
                # Audiveris occasionally splits a stack across two <stack> entries
                # (e.g. cautionary time-signature column). Union the bbox.
                existing.ulx = min(existing.ulx, ulx)
                existing.lrx = max(existing.lrx, lrx)
                existing.uly = min(existing.uly, y_min)
                existing.lry = max(existing.lry, y_max)

    sheet.measure_zones = sorted(by_stack_id.values(), key=lambda z: z.stack_id)
    return sheet


def _system_y_extent(system: etree._Element) -> tuple[int | None, int | None]:
    """Vertical extent of a system: min/max y across all staff line points.

    Falls back to ``None`` if the system has no parseable line points.
    """
    ys: list[int] = []
    for point in system.iterfind(".//part/staff/lines/line/point"):
        try:
            ys.append(int(point.get("y")))
        except (TypeError, ValueError):
            continue
    if not ys:
        return None, None
    return min(ys), max(ys)


def extract_binary_image(omr_path: Path | str, sheet_num: int, dest_path: Path | str) -> Path:
    """Extract the BINARY.png for one sheet from a .omr zip into ``dest_path``."""
    omr_path = Path(omr_path)
    dest_path = Path(dest_path)
    member = f"sheet#{sheet_num}/BINARY.png"
    with zipfile.ZipFile(omr_path, "r") as zf:
        with zf.open(member) as src, dest_path.open("wb") as dst:
            dst.write(src.read())
    return dest_path


def iter_zones(data: OmrData) -> Iterable[MeasureZone]:
    """Yield zones in book reading order: by sheet, then stack id."""
    for sheet in data.sheets:
        yield from sheet.measure_zones
