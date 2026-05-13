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

from .iiif_loader import IIIFManifest
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


def _rename_xmlids(subtree: etree._Element, prefix: str) -> None:
    """Prefix every ``xml:id`` in ``subtree`` and rewrite intra-subtree
    fragment references (``#oldId``) to point at the renamed IDs.

    Verovio assigns the same xml:id values (``P1``, ``s1m0``, ...) to every
    MEI it produces from a single .mxl, so naively concatenating multiple
    Verovio outputs would collide on those IDs.
    """
    xmlid = f"{{{XML_NS}}}id"
    renames: dict[str, str] = {}
    for el in subtree.iter():
        old = el.get(xmlid)
        if old is not None and old not in renames:
            new = f"{prefix}{old}"
            el.set(xmlid, new)
            renames[old] = new
    if not renames:
        return
    for el in subtree.iter():
        for attr, value in list(el.attrib.items()):
            if not value or "#" not in value:
                continue
            tokens = value.split()
            rewritten = []
            changed = False
            for tok in tokens:
                if tok.startswith("#") and tok[1:] in renames:
                    rewritten.append(f"#{renames[tok[1:]]}")
                    changed = True
                else:
                    rewritten.append(tok)
            if changed:
                el.set(attr, " ".join(rewritten))


def merge_mei_movements(mei_xmls: list[str]) -> str:
    """Stitch per-movement Verovio MEI outputs into one multi-mdiv MEI.

    Audiveris exports one ``.mxl`` per movement when it detects a section
    break. We concatenate the resulting per-mvt MEIs by appending each later
    document's ``<mdiv>`` elements to the first document's ``<body>``. The
    first document's ``<meiHead>`` is kept verbatim.

    xml:ids inside appended mdivs are namespaced with an ``mN_`` prefix to
    avoid colliding with the first MEI's own ids.

    Positional pairing with OMR zones happens later in ``inject_facsimile``,
    which walks ``.//measure`` in document order — so the order of
    ``mei_xmls`` matters and must match book reading order.
    """
    if not mei_xmls:
        raise ValueError("merge_mei_movements requires at least one MEI string")
    if len(mei_xmls) == 1:
        return mei_xmls[0]

    parser = etree.XMLParser(remove_blank_text=False)
    roots = [etree.fromstring(xml.encode("utf-8"), parser) for xml in mei_xmls]
    base_body = roots[0].find(f".//{{{MEI_NS}}}body")
    if base_body is None:
        raise ValueError("First MEI has no <body>; cannot merge movements")

    counter = 0
    for mdiv in base_body.findall(f"{{{MEI_NS}}}mdiv"):
        counter += 1
        mdiv.set("n", str(counter))

    for root in roots[1:]:
        body = root.find(f".//{{{MEI_NS}}}body")
        if body is None:
            continue
        for mdiv in body.findall(f"{{{MEI_NS}}}mdiv"):
            counter += 1
            mdiv.set("n", str(counter))
            _rename_xmlids(mdiv, f"m{counter}_")
            base_body.append(mdiv)

    return etree.tostring(
        roots[0],
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    ).decode("utf-8")


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


def _mei(tag: str) -> str:
    return f"{{{MEI_NS}}}{tag}"


def _ensure_child(parent: etree._Element, tag: str, position: int | None = None) -> etree._Element:
    existing = parent.find(_mei(tag))
    if existing is not None:
        return existing
    el = etree.Element(_mei(tag))
    if position is None:
        parent.append(el)
    else:
        parent.insert(position, el)
    return el


def inject_meihead_metadata(
    mei_xml: str,
    manifest: IIIFManifest,
    manifest_url: str | None = None,
) -> str:
    """Populate ``<meiHead>`` with metadata sourced from an IIIF manifest.

    Verovio's auto-generated ``meiHead`` carries no real metadata, so we:
    - set ``fileDesc/titleStmt/title`` to the manifest label (the only
      sensible default for "what is this MEI file")
    - replace ``fileDesc/sourceDesc`` with one ``<source>`` element that
      records provider, rights, required attribution, and the manifest URL
      as a back-link
    """
    parser = etree.XMLParser(remove_blank_text=False)
    root = etree.fromstring(mei_xml.encode("utf-8"), parser)
    head = root.find(_mei("meiHead"))
    if head is None:
        head = etree.Element(_mei("meiHead"))
        root.insert(0, head)

    file_desc = _ensure_child(head, "fileDesc", position=0)
    title_stmt = _ensure_child(file_desc, "titleStmt", position=0)
    title = _ensure_child(title_stmt, "title")
    title.text = manifest.label or "(untitled)"

    for existing in file_desc.findall(_mei("sourceDesc")):
        file_desc.remove(existing)

    source_desc = etree.SubElement(file_desc, _mei("sourceDesc"))
    source = etree.SubElement(source_desc, _mei("source"))

    src_title_stmt = etree.SubElement(source, _mei("titleStmt"))
    src_title = etree.SubElement(src_title_stmt, _mei("title"))
    src_title.text = manifest.label or "(untitled)"
    if manifest.provider:
        resp_stmt = etree.SubElement(src_title_stmt, _mei("respStmt"))
        corp = etree.SubElement(resp_stmt, _mei("corpName"))
        corp.set("role", "provider")
        corp.text = manifest.provider

    if manifest.rights or manifest.required_statement:
        pub_stmt = etree.SubElement(source, _mei("pubStmt"))
        if manifest.rights:
            availability = etree.SubElement(pub_stmt, _mei("availability"))
            use = etree.SubElement(availability, _mei("useRestrict"))
            use.text = manifest.rights
        if manifest.required_statement:
            attribution = etree.SubElement(pub_stmt, _mei("respStmt"))
            resp = etree.SubElement(attribution, _mei("resp"))
            resp.text = "attribution"
            name = etree.SubElement(attribution, _mei("name"))
            name.text = manifest.required_statement

    if manifest_url:
        bibl = etree.SubElement(source, _mei("bibl"))
        ref = etree.SubElement(bibl, _mei("ref"))
        ref.set("target", manifest_url)
        ref.text = "IIIF Manifest"

    return etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    ).decode("utf-8")
