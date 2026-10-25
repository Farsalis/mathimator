"""Quick Manim renders of the render-smoke projects: every beat kind, shape and easing (section 7, Beats and shapes).

One supervisor opens a copy of each project in ``tests/fixtures/render-smoke``
under ``out/beats``, or the folder ``MATHIMATOR_BEATS_OUT`` names, and previews
it at the preview profile, 854×480 at 15 fps. A preview passes with ``done``,
one ``beat_ready`` per beat in beat order and a movie within one frame of the
beats' total, and its scene plays no group (section 5). The cache scenes are
previewed three times with no edit, and Manim writes no new partial movie
after the first render: a rotate, a wave, a move along a path, a wiggle and a
camera move leave Manim's key for every later play as it was (sections 5 and
10). The seconds each preview took are written to ``render-times.json`` there.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from mathimator_worker.document import PREVIEW_FPS, PREVIEW_HEIGHT, PREVIEW_WIDTH, Project, load_text
from mathimator_worker.layout import ProjectLayout
from tests.smoke_projects import CACHE_SCENES, FOLDER
from tests.support import REPO, SupervisorClient, fixture_project, probe_video, start_supervisor

pytestmark = pytest.mark.render

OUT = Path(os.environ.get("MATHIMATOR_BEATS_OUT") or REPO / "out" / "beats")
NAMES = sorted(path.name for path in FOLDER.iterdir())
RENDER_TIMEOUT_S = 900


@pytest.fixture(scope="module")
def supervisor() -> Iterator[SupervisorClient]:
    if OUT.exists():
        shutil.rmtree(OUT)
    client = start_supervisor(OUT / "start", OUT / "logs" / "worker.log", init=True)
    try:
        client.send({"cmd": "status", "id": "status"})
        client.wait_for(lambda event: event.get("event") == "ready" and event.get("jobId") == "status", 30)
        yield client
    finally:
        client.close()


@pytest.fixture(scope="module")
def render_times(supervisor: SupervisorClient) -> Iterator[dict[str, float]]:
    """The seconds each preview took by job, written to ``render-times.json`` once the module's tests are done."""
    times: dict[str, float] = {}
    yield times
    (OUT / "render-times.json").write_text(json.dumps(times, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("name", NAMES)
def test_every_smoke_project_previews_one_partial_per_beat(
    supervisor: SupervisorClient, render_times: dict[str, float], name: str
) -> None:
    project = _open(supervisor, name, OUT / name)
    render_times[name] = _preview(supervisor, project, f"{name}-preview")
    scene = ProjectLayout(OUT / name).scene_path.read_text(encoding="utf-8")
    assert [line for line in scene.splitlines() if "self.play(" in line and "VGroup(" in line] == []


@pytest.mark.parametrize("name", CACHE_SCENES)
def test_a_preview_rendered_again_with_no_edit_writes_no_new_partial(
    supervisor: SupervisorClient, render_times: dict[str, float], name: str
) -> None:
    project = _open(supervisor, name, OUT / f"{name}-again")
    partials = ProjectLayout(OUT / f"{name}-again").partial_movie_dir(f"{PREVIEW_HEIGHT}p{PREVIEW_FPS}")
    written = []
    for render in range(3):
        job_id = f"{name}-again-{render}"
        render_times[job_id] = _preview(supervisor, project, job_id)
        written.append(sorted(path.name for path in partials.glob("*.mp4")))
    assert written[0], partials
    assert written[1:] == [written[0], written[0]]


def _open(client: SupervisorClient, name: str, folder: Path) -> Project:
    """Bind a fresh copy of the smoke project ``name`` in ``folder``, and return its document."""
    fixture_project(folder, FOLDER / name / "project.json")
    client.send({"cmd": "open_project", "id": f"open-{folder.name}", "path": str(folder)})
    opened = client.wait_for(lambda event: event.get("jobId") == f"open-{folder.name}", 30)
    if opened.get("event") != "state":
        raise AssertionError(json.dumps(opened, indent=2))
    return load_text((folder / "project.json").read_text(encoding="utf-8"))


def _preview(client: SupervisorClient, project: Project, job_id: str) -> float:
    """Preview the bound ``project`` as ``job_id``: done, a beat_ready per beat in order, and the movie's length.

    Returns the seconds from the request to the job's end.
    """
    started = time.perf_counter()
    client.send({"cmd": "render_preview", "id": job_id})
    finished = client.wait_for_end(job_id, RENDER_TIMEOUT_S)
    seconds = time.perf_counter() - started
    if finished.get("event") != "done":
        raise AssertionError(json.dumps(finished, indent=2))
    ready = [event for event in client.events if event.get("event") == "beat_ready" and event.get("jobId") == job_id]
    beats = project.scene.beats
    assert [event["beatIndex"] for event in ready] == list(range(len(beats)))
    assert all(Path(str(event["path"])).is_file() for event in ready)
    duration, width, height = probe_video(Path(str(finished["path"])))
    assert (width, height) == (PREVIEW_WIDTH, PREVIEW_HEIGHT)
    assert abs(duration - sum(beat.duration for beat in beats)) <= 1 / PREVIEW_FPS
    return seconds
