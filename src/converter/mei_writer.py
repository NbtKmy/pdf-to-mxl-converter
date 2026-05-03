"""MusicXML → MEI conversion via Verovio.

The Verovio call runs in a subprocess so a C++ abort (e.g.
``std::out_of_range`` triggered by messy Audiveris MusicXML) becomes a
non-zero exit code rather than killing the Flask process.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

VEROVIO_TIMEOUT_SECONDS = 180

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
