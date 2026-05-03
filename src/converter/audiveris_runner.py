"""Run Audiveris inside the sibling docker container."""
from __future__ import annotations

from dataclasses import dataclass

import docker

AUDIVERIS_CONTAINER = "audiveris"
AUDIVERIS_CMD = (
    '/bin/sh -c "/Audiveris/bin/Audiveris '
    '-batch -export -output /output /input/*"'
)


class AudiverisError(RuntimeError):
    def __init__(self, exit_code: int, log: str):
        self.exit_code = exit_code
        self.log = log
        super().__init__(f"Audiveris exited with code {exit_code}")


@dataclass
class AudiverisResult:
    exit_code: int
    log: str


def run_audiveris() -> AudiverisResult:
    """Invoke Audiveris on /input/* inside the container.

    Audiveris produces the exported MusicXML (.mxl) under /output. The host
    side mounts that folder so we can read it immediately after the call.
    """
    client = docker.from_env()
    container = client.containers.get(AUDIVERIS_CONTAINER)
    exit_code, output = container.exec_run(AUDIVERIS_CMD)
    log = output.decode(errors="replace") if output else ""
    return AudiverisResult(exit_code=exit_code, log=log)
