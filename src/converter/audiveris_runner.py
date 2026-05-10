"""Run Audiveris inside the sibling docker container."""
from __future__ import annotations

from dataclasses import dataclass

import docker
from docker.errors import NotFound

AUDIVERIS_CONTAINER = "audiveris"


class AudiverisError(RuntimeError):
    def __init__(self, exit_code: int, log: str):
        self.exit_code = exit_code
        self.log = log
        super().__init__(f"Audiveris exited with code {exit_code}")


@dataclass
class AudiverisResult:
    exit_code: int
    log: str


def _ensure_running(client: docker.DockerClient):
    try:
        container = client.containers.get(AUDIVERIS_CONTAINER)
    except NotFound as exc:
        raise RuntimeError(
            f"audiveris container '{AUDIVERIS_CONTAINER}' is not present. "
            "Run `docker compose up` first."
        ) from exc
    if container.status != "running":
        container.start()
        container.reload()
    return container


def run_audiveris(input_dir: str = "/input", output_dir: str = "/output") -> AudiverisResult:
    """Invoke Audiveris on ``<input_dir>/*`` inside the audiveris container.

    Audiveris writes ``.mxl`` and ``.omr`` artifacts under ``<output_dir>``.
    Both paths are container-absolute (the audiveris container side); the
    flask container is responsible for ensuring those paths resolve to the
    expected job-scoped directories via the shared named volumes.

    The audiveris container is a long-lived sibling that we ``exec_run`` into
    per job. It can be killed (OOM, Docker Desktop restart) between jobs, so
    we re-start it if needed before exec'ing.
    """
    cmd = (
        f'/bin/sh -c "/Audiveris/bin/Audiveris '
        f'-batch -export -save -output {output_dir} {input_dir}/*"'
    )
    client = docker.from_env()
    container = _ensure_running(client)
    exit_code, output = container.exec_run(cmd)
    log = output.decode(errors="replace") if output else ""
    return AudiverisResult(exit_code=exit_code, log=log)
