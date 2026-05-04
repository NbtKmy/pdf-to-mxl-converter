"""MusicXML → MEI conversion via Verovio, plus facsimile injection.

The Verovio call runs in a subprocess so a C++ abort (e.g.
``std::out_of_range`` triggered by messy Audiveris MusicXML) becomes a
non-zero exit code rather than killing the Flask process.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from lxml import etree

from .omr_parser import OmrData, iter_zones

VEROVIO_TIMEOUT_SECONDS = 180

MEI_NS = "http://www.music-encoding.org/ns/mei"
XML_NS = "http://www.w3.org/XML/1998/namespace"

_VEROVIO_WORKER = (
    "import sys, verovio\n"
    "tk = verovio.toolkit()\n"
    "if not tk.loadFile(sys.argv[1]):\n"
    "    sys.stderr.write('Verovio loadFile returned False\\n')\n"
    "    sys.exit(2)\n"
    "sys.stdout.write(tk.getMEI({'scoreBased': True}))\n"
    "sys.stdout.flush()\n"
)


def musicxml_to_mei(mxl_path: Path) -> str:
    try:
        result = subprocess.run(
            [sys.executable, "-c", _VEROVIO_WORKER, str(mxl_path)],
            capture_output=True,
            text=True,
            timeout=VEROVIO_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Verovio MusicXML→MEI conversion timed out after {VEROVIO_TIMEOUT_SECONDS}s"
        ) from exc

    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
        crash_hint = (
            "Verovio crashed while reading the MusicXML "
            "(likely too many recognition errors from Audiveris). "
            "Try re-running with the MusicXML output format instead."
        )
        if detail:
            raise RuntimeError(f"{crash_hint} [{detail}]")
        raise RuntimeError(crash_hint)
    return result.stdout


def inject_facsimile(
    mei_xml: str,
    omr_data: OmrData,
    sheet_image_urls: dict[int, str] | None = None,
) -> str:
    """Inject a ``<facsimile>`` section into a Verovio-produced MEI string.

    For each sheet in ``omr_data`` we create a ``<surface>`` containing one
    ``<graphic>`` (the source image) and one ``<zone>`` per measure. The MEI's
    own ``<measure>`` elements are then walked in document order and paired
    with zones in book reading order — each measure receives ``facs="#zone-mN"``.

    Pairing is positional: the n-th MEI measure (across all parts/movements)
    pairs with the n-th OMR zone (sheet 1's stacks in id order, then sheet 2's,
    etc.). If counts disagree, only the overlapping prefix gets ``facs``; the
    extra side is silently left unmapped.

    ``sheet_image_urls`` maps ``sheet_num`` → the URL written to
    ``<graphic target=...>``. Sheets without an entry get an empty target,
    which the caller can fill in later.
    """
    sheet_image_urls = sheet_image_urls or {}

    parser = etree.XMLParser(remove_blank_text=False)
    root = etree.fromstring(mei_xml.encode("utf-8"), parser)
    music = root.find(f"{{{MEI_NS}}}music")
    if music is None:
        raise ValueError("MEI has no <music> element; cannot inject facsimile")

    # Drop any pre-existing <facsimile> so this function is idempotent.
    for existing in music.findall(f"{{{MEI_NS}}}facsimile"):
        music.remove(existing)

    all_zones = list(iter_zones(omr_data))
    zone_id_by_key: dict[tuple[int, int], str] = {}
    for idx, z in enumerate(all_zones):
        zone_id_by_key[(z.sheet_num, z.stack_id)] = f"zone-m{idx}"

    facsimile = etree.Element(f"{{{MEI_NS}}}facsimile")
    for sheet in omr_data.sheets:
        surface = etree.SubElement(
            facsimile,
            f"{{{MEI_NS}}}surface",
            n=str(sheet.sheet_num),
            ulx="0",
            uly="0",
            lrx=str(sheet.width),
            lry=str(sheet.height),
        )
        etree.SubElement(
            surface,
            f"{{{MEI_NS}}}graphic",
            target=sheet_image_urls.get(sheet.sheet_num, ""),
            width=str(sheet.width),
            height=str(sheet.height),
        )
        for z in sheet.measure_zones:
            zone = etree.SubElement(
                surface,
                f"{{{MEI_NS}}}zone",
                ulx=str(z.ulx),
                uly=str(z.uly),
                lrx=str(z.lrx),
                lry=str(z.lry),
            )
            zone.set(f"{{{XML_NS}}}id", zone_id_by_key[(z.sheet_num, z.stack_id)])

    # <facsimile> must be the first child of <music> (before <body>).
    music.insert(0, facsimile)

    measures = root.findall(f".//{{{MEI_NS}}}measure")
    for measure, zone in zip(measures, all_zones):
        measure.set("facs", f"#{zone_id_by_key[(zone.sheet_num, zone.stack_id)]}")

    return etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    ).decode("utf-8")
