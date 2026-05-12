"""IIIF Presentation API v2 / v3 manifest fetching and parsing.

A "manifest" describes a single object (a printed book, a music album,
etc.) as an ordered list of "canvases" — typically one canvas per page.
Each canvas references an Image API service that can serve the full-res
page image at arbitrary crops and scales.

For the OMR pipeline we only need, per canvas:
    - a human-readable label
    - a thumbnail URL (for the selection grid)
    - a full-resolution image URL (for Audiveris)
    - width / height

We also surface manifest-level metadata (label, rights, provider,
requiredStatement) — Phase 5 Step 4 will map these into MEI ``<meiHead>``,
and the editor will eventually display attribution from them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

USER_AGENT = (
    "pdf-to-mxl-converter/0.5 "
    "(+https://github.com/Nobutake-Kamiya/pdf-to-mxl-converter; "
    "OMR pipeline for institutional IIIF scores)"
)

# Audiveris rejects sheets over ~20 MP. Requesting smaller images via
# the Image API's size parameter keeps us under the cap and saves bandwidth.
DEFAULT_MAX_WIDTH = 2400

DEFAULT_TIMEOUT_SECONDS = 30


class IIIFError(RuntimeError):
    pass


@dataclass
class IIIFCanvas:
    index: int            # 1-origin position in the manifest
    label: str
    thumbnail_url: str | None
    image_url: str        # natural-size image URL; may exceed Audiveris cap
    service_url: str | None  # Image API base, if discoverable
    width: int
    height: int


@dataclass
class IIIFManifest:
    label: str
    canvases: list[IIIFCanvas]
    rights: str | None = None
    provider: str | None = None
    required_statement: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def fetch_manifest(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> IIIFManifest:
    if not url or not _is_http_url(url):
        raise IIIFError(f"Manifest URL must be http(s): {url!r}")
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise IIIFError(f"Failed to fetch manifest: {exc}") from exc
    except ValueError as exc:
        raise IIIFError(f"Manifest is not valid JSON: {exc}") from exc
    return parse_manifest(data)


def parse_manifest(data: dict[str, Any]) -> IIIFManifest:
    if not isinstance(data, dict):
        raise IIIFError("Manifest root must be a JSON object")
    if _is_v3(data):
        return _parse_v3(data)
    if _is_v2(data):
        return _parse_v2(data)
    raise IIIFError(
        "Could not detect IIIF Presentation API version. "
        "Expected `sequences` (v2) or `items` (v3) at the manifest root."
    )


def download_image(
    canvas: IIIFCanvas,
    dest_path: Path,
    max_width: int = DEFAULT_MAX_WIDTH,
    timeout: float = DEFAULT_TIMEOUT_SECONDS * 4,
) -> Path:
    """Download a canvas's full-res image to ``dest_path``.

    Prefers the Image API ``service`` URL (which can resize on the server)
    over the natural-size annotation URL. If the canvas has no service,
    we fall back to fetching ``image_url`` as-is.
    """
    url = _scaled_image_url(canvas, max_width) or canvas.image_url
    if not url:
        raise IIIFError(f"Canvas #{canvas.index} has no resolvable image URL")
    try:
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            with dest_path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
    except requests.RequestException as exc:
        raise IIIFError(f"Image download failed for canvas #{canvas.index}: {exc}") from exc
    return dest_path


# --- v2 ---------------------------------------------------------------------

def _is_v2(data: dict) -> bool:
    return "sequences" in data and isinstance(data.get("sequences"), list)


def _parse_v2(data: dict) -> IIIFManifest:
    canvases: list[IIIFCanvas] = []
    for seq in data.get("sequences", []):
        for raw_canvas in seq.get("canvases", []) or []:
            canvases.append(_parse_v2_canvas(raw_canvas, len(canvases) + 1))
    return IIIFManifest(
        label=_to_label_string(data.get("label")) or "(untitled)",
        canvases=canvases,
        rights=_first_str(data.get("license")) or _first_str(data.get("rights")),
        provider=_first_str(data.get("attribution")),
        required_statement=_first_str(data.get("attribution")),
        raw=data,
    )


def _parse_v2_canvas(c: dict, index: int) -> IIIFCanvas:
    label = _to_label_string(c.get("label")) or f"Page {index}"
    width = int(c.get("width") or 0)
    height = int(c.get("height") or 0)

    image_url = ""
    service_url = None
    for img in c.get("images", []) or []:
        resource = img.get("resource") or {}
        image_url = image_url or resource.get("@id", "")
        svc = resource.get("service")
        if isinstance(svc, dict):
            service_url = service_url or svc.get("@id")
        elif isinstance(svc, list) and svc:
            service_url = service_url or svc[0].get("@id")

    thumbnail_url = _extract_thumbnail(c.get("thumbnail"))
    return IIIFCanvas(
        index=index,
        label=label,
        thumbnail_url=thumbnail_url,
        image_url=image_url,
        service_url=service_url,
        width=width,
        height=height,
    )


# --- v3 ---------------------------------------------------------------------

def _is_v3(data: dict) -> bool:
    return "items" in data and isinstance(data.get("items"), list) and \
        any(_get_type(item) == "Canvas" for item in data["items"])


def _parse_v3(data: dict) -> IIIFManifest:
    canvases: list[IIIFCanvas] = []
    for raw_canvas in data.get("items", []):
        if _get_type(raw_canvas) != "Canvas":
            continue
        canvases.append(_parse_v3_canvas(raw_canvas, len(canvases) + 1))
    provider = None
    raw_provider = data.get("provider")
    if isinstance(raw_provider, list) and raw_provider:
        provider = _to_label_string(raw_provider[0].get("label"))
    required = _to_label_string((data.get("requiredStatement") or {}).get("value"))
    return IIIFManifest(
        label=_to_label_string(data.get("label")) or "(untitled)",
        canvases=canvases,
        rights=data.get("rights") if isinstance(data.get("rights"), str) else None,
        provider=provider,
        required_statement=required,
        raw=data,
    )


def _parse_v3_canvas(c: dict, index: int) -> IIIFCanvas:
    label = _to_label_string(c.get("label")) or f"Page {index}"
    width = int(c.get("width") or 0)
    height = int(c.get("height") or 0)

    image_url = ""
    service_url = None
    for ann_page in c.get("items", []) or []:
        for ann in ann_page.get("items", []) or []:
            body = ann.get("body") or {}
            image_url = image_url or body.get("id", "")
            svc = body.get("service")
            if isinstance(svc, list) and svc:
                service_url = service_url or (svc[0].get("id") or svc[0].get("@id"))

    thumbnail_url = _extract_thumbnail(c.get("thumbnail"))
    return IIIFCanvas(
        index=index,
        label=label,
        thumbnail_url=thumbnail_url,
        image_url=image_url,
        service_url=service_url,
        width=width,
        height=height,
    )


# --- shared helpers ---------------------------------------------------------

def _get_type(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    return obj.get("type") or obj.get("@type") or ""


def _to_label_string(label: Any) -> str:
    """Flatten any of the IIIF label/value shapes into one display string."""
    if label is None:
        return ""
    if isinstance(label, str):
        return label
    if isinstance(label, list):
        parts: list[str] = []
        for item in label:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if "@value" in item:
                    parts.append(str(item["@value"]))
                elif "value" in item:
                    parts.append(str(item["value"]))
        return "; ".join(p for p in parts if p)
    if isinstance(label, dict):
        # v3 language map: {lang: [strings]} — prefer "en", "none", else first
        for lang in ("en", "none", "@none"):
            if lang in label:
                return "; ".join(str(s) for s in label[lang])
        for val in label.values():
            if isinstance(val, list):
                return "; ".join(str(s) for s in val)
            if isinstance(val, str):
                return val
    return ""


def _first_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for v in value:
            if isinstance(v, str):
                return v
    return None


def _extract_thumbnail(thumb: Any) -> str | None:
    if thumb is None:
        return None
    if isinstance(thumb, str):
        return thumb
    if isinstance(thumb, dict):
        return thumb.get("@id") or thumb.get("id")
    if isinstance(thumb, list) and thumb:
        first = thumb[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("id") or first.get("@id")
    return None


_IMAGE_API_REGION_RE = re.compile(r"/(full|square|\d+,\d+,\d+,\d+|pct:[\d.,]+)/")


def _scaled_image_url(canvas: IIIFCanvas, max_width: int) -> str | None:
    """Build an Image API URL that requests a width-capped image.

    Returns None if the canvas has no usable service URL — caller falls
    back to ``canvas.image_url``.
    """
    if not canvas.service_url or canvas.width <= 0:
        return None
    target_w = min(canvas.width, max_width)
    base = canvas.service_url.rstrip("/")
    # v2: /full/{w},/0/default.jpg ; v3 prefers same shape too
    return f"{base}/full/{target_w},/0/default.jpg"


def _is_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False
