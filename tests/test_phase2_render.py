"""Timed Manim renders for phase 2.

Cold partials means ``videos/`` is deleted. TeX output under the same media
directory is left in place. The 4.770 s figure is this VM's 1.5× cap against
phase 1's 3.180 s preview, measured the same way. 67.5 s is a defect.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from mathimator_worker.document import load_text
from mathimator_worker.identity import export_folder_name, export_key
from tests.support import (
    REPO,
    TERMINAL_EVENTS,
    SupervisorClient,
    fixture_project,
    media_dir,
    probe_video,
    sha256_file,
    start_supervisor,
)

pytestmark = pytest.mark.render

PHASE2 = REPO / "out" / "phase2"
FIXTURES = REPO / "tests" / "fixtures"
REFERENCE_JSON = FIXTURES / "reference-60" / "project.json"
PREVIEW_LIMIT_S = 67.5
RELATIVE_CAP_S = 4.770


def test_reference_60_codegen_preview_export_and_partial() -> None:
    project = fixture_project(PHASE2 / "reference", REFERENCE_JSON)
    log_dir = PHASE2 / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    warm = _render(project, log_dir / "warm.log", "render_preview", "warm", 600)
    assert warm["event"] == "done"
    videos = media_dir(project) / "videos"
    shutil.rmtree(videos)

    started = time.perf_counter()
    preview = _render(project, log_dir / "preview.log", "render_preview", "preview", 600)
    preview_seconds = time.perf_counter() - started
    assert preview["event"] == "done"
    preview_path = Path(str(preview["path"]))
    beats = preview["beats"]
    assert isinstance(beats, list)
    assert [event["beatIndex"] for event in beats] == list(range(8))
    partials = [Path(str(event["path"])) for event in beats]
    assert len(set(partials)) == 8
    duration, width, height = probe_video(preview_path)
    assert (width, height) == (854, 480)
    assert abs(duration - 60.0) <= (1.0 / 15.0)
    assert preview_seconds < PREVIEW_LIMIT_S
    preview_hashes = [sha256_file(path) for path in partials]

    for quality in media_dir(project).glob("videos/*/1080p30"):
        shutil.rmtree(quality)
    export_started = time.perf_counter()
    exported = _render(project, log_dir / "export.log", "export", "export", 600)
    export_seconds = time.perf_counter() - export_started
    assert exported["event"] == "done"
    export_path = Path(str(exported["path"]))
    export_duration, export_width, export_height = probe_video(export_path)
    assert (export_width, export_height) == (1920, 1080)
    assert abs(export_duration - 60.0) <= (1.0 / 30.0)
    assert "exports" in export_path.parts
    assert export_path.resolve() != preview_path.resolve()
    assert sha256_file(export_path) != sha256_file(preview_path)
    loaded = load_text((project / "project.json").read_text(encoding="utf-8"))
    assert export_path.parent.name == export_folder_name(loaded)
    manifest = json.loads((export_path.parent / "export.json").read_text(encoding="utf-8"))
    assert manifest["exportKey"] == export_key(loaded)
    assert "480p15" not in export_path.parts

    setup = _edit_and_render(
        project,
        log_dir / "shorten.log",
        {"op": "set_beat_duration", "beatId": "beat_8", "duration": 5},
        "shorten",
        timed=False,
    )
    assert setup["event"] == "done"
    assert _beat(setup, 7)["duration"] == 5
    shortened = setup["beats"]
    assert isinstance(shortened, list)
    assert [event["beatIndex"] for event in shortened] == list(range(8))
    prefix_hashes = [sha256_file(Path(str(event["path"]))) for event in shortened[:7]]

    colored = _edit_and_render(
        project,
        log_dir / "recolor.log",
        {"op": "set_beat_color", "beatId": "beat_8", "color": "#81B29A"},
        "recolor",
        timed=True,
        until_beat=7,
    )
    assert colored["event"] == "beat_ready"
    assert colored["beatIndex"] == 7
    assert _beat(colored, 7)["params"] == {"color": "#81B29A"}
    recolor_beats = colored["beats"]
    assert isinstance(recolor_beats, list)
    assert [event["beatIndex"] for event in recolor_beats[:7]] == list(range(7))
    recolor_hashes = [sha256_file(Path(str(event["path"]))) for event in recolor_beats[:7]]
    assert recolor_hashes == prefix_hashes
    assert sha256_file(Path(str(recolor_beats[7]["path"]))) != sha256_file(Path(str(shortened[7]["path"])))

    _write(
        PHASE2 / "timings.json",
        {
            "preview_seconds": preview_seconds,
            "phase1_preview_seconds": 3.180,
            "relative_cap_seconds": RELATIVE_CAP_S,
            "absolute_cap_seconds": PREVIEW_LIMIT_S,
            "within_relative_cap": preview_seconds <= RELATIVE_CAP_S,
            "preview_path": str(preview_path),
            "preview_duration": duration,
            "preview_size": [width, height],
            "preview_partial_hashes": preview_hashes,
            "export_seconds": export_seconds,
            "export_path": str(export_path),
            "export_duration": export_duration,
            "export_size": [export_width, export_height],
            "export_hash": sha256_file(export_path),
            "preview_hash": sha256_file(preview_path),
            "partial_rerender_seconds": colored["seconds"],
            "partial_prefix_hashes_unchanged": True,
            "partial_prefix_hashes": prefix_hashes,
        },
    )


def test_axes_function_insert_preview() -> None:
    project = PHASE2 / "template"
    if project.exists():
        shutil.rmtree(project)
    log_path = PHASE2 / "logs" / "template.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    reference_tex = PHASE2 / "reference" / ".mathimator" / "cache" / "media" / "Tex"
    client = start_supervisor(project, log_path, init=True)
    try:
        _ready(client, "template-status")
        destination = media_dir(project) / "Tex"
        if reference_tex.is_dir() and not destination.exists():
            shutil.copytree(reference_tex, destination)
        videos = media_dir(project) / "videos"
        if videos.exists():
            shutil.rmtree(videos)
        started = time.perf_counter()
        client.send({"cmd": "edit", "id": "insert", "edit": {"op": "add_axes_function"}})
        inserted = client.wait_for(lambda event: event.get("jobId") == "insert", 30)
        if inserted.get("event") != "state":
            raise AssertionError(json.dumps(inserted, indent=2))
        time.sleep(0.3)
        client.send({"cmd": "render_preview", "id": "insert-preview"})
        finished = client.wait_for_end("insert-preview", 180)
        if finished.get("event") != "done":
            raise AssertionError(json.dumps(finished, indent=2))
        seconds = time.perf_counter() - started
        beats = _beats(client, "insert-preview")
    finally:
        client.close()
    assert [event["beatIndex"] for event in beats] == [0, 1]
    for event in beats:
        assert Path(str(event["path"])).is_file()
    _write(
        PHASE2 / "template-insert.json",
        {
            "seconds": seconds,
            "includes_300ms_debounce": True,
            "output": finished["path"],
            "partials": [str(event["path"]) for event in beats],
            "reference_budget_seconds": 8,
        },
    )


def _ready(client: SupervisorClient, job_id: str) -> None:
    client.send({"cmd": "status", "id": job_id})
    client.wait_for(lambda event: event.get("event") == "ready" and event.get("jobId") == job_id, 30)


def _render(
    project: Path,
    log_path: Path,
    command: str,
    job_id: str,
    timeout: float,
) -> dict[str, object]:
    if log_path.exists():
        log_path.unlink()
    client = start_supervisor(project, log_path)
    try:
        _ready(client, f"status-{job_id}")
        client.send({"cmd": command, "id": job_id})
        finished = client.wait_for_end(job_id, timeout)
        if finished.get("event") != "done":
            raise AssertionError(json.dumps(finished, indent=2))
        return {"event": "done", "path": finished["path"], "beats": _beats(client, job_id)}
    finally:
        client.close()


def _edit_and_render(
    project: Path,
    log_path: Path,
    edit: dict[str, object],
    job_id: str,
    *,
    timed: bool,
    until_beat: int | None = None,
) -> dict[str, object]:
    if log_path.exists():
        log_path.unlink()
    client = start_supervisor(project, log_path)
    try:
        _ready(client, f"status-{job_id}")
        started = time.perf_counter()
        client.send({"cmd": "edit", "id": f"edit-{job_id}", "edit": edit})
        edited = client.wait_for(lambda event: event.get("jobId") == f"edit-{job_id}", 30)
        if edited.get("event") != "state":
            raise AssertionError(json.dumps(edited, indent=2))
        if timed:
            time.sleep(0.3)
        else:
            started = time.perf_counter()
        client.send({"cmd": "render_preview", "id": job_id})
        if until_beat is None:
            finished = client.wait_for_end(job_id, 600)
        else:
            finished = client.wait_for(
                lambda event: (
                    event.get("jobId") == job_id
                    and (event.get("beatIndex") == until_beat or event.get("event") in TERMINAL_EVENTS)
                ),
                600,
            )
        seconds = time.perf_counter() - started
        ended = client.wait_for_end(job_id, 600)
        if ended.get("event") != "done":
            raise AssertionError(json.dumps(ended, indent=2))
        return {
            "event": finished["event"],
            "beatIndex": finished.get("beatIndex"),
            "path": finished.get("path"),
            "seconds": seconds,
            "document": edited["document"],
            "beats": _beats(client, job_id),
        }
    finally:
        client.close()


def _beat(rendered: dict[str, object], index: int) -> dict[str, Any]:
    """Beat ``index`` of the document the edit before ``rendered`` saved."""
    document = rendered["document"]
    assert isinstance(document, dict)
    beat: dict[str, Any] = document["scene"]["beats"][index]
    return beat


def _beats(client: object, job_id: str) -> list[dict[str, object]]:
    assert isinstance(client, SupervisorClient)
    return [event for event in client.events if event.get("event") == "beat_ready" and event.get("jobId") == job_id]


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
