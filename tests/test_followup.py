"""Follow-up fixes that do not call Win32.

The Job Object tests live in ``test_win32_job.py`` and stay skipped unless
``MATHIMATOR_WIN32_TESTS=1``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mathimator_worker.clip_check import ClipChecker
from mathimator_worker.document import dumps_project, empty_project, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.progress import MANIFEST, discard_written, read_stamps, run_files, stamps_record
from mathimator_worker.session import Session
from mathimator_worker.supervisor import Supervisor, _Job
from mathimator_worker.win32_job import jobs_enabled
from tests.support import (
    SupervisorClient,
    complete_clip,
    link_folder,
    read_argv,
    start_supervisor,
    write_fake_interpreter,
)


def test_delete_object_removes_targeting_beats_as_one_undo() -> None:
    session = Session(empty_project())
    session.edit({"op": "add_axes_function"})
    plot_id = next(item.id for item in session.project.scene.objects if item.type == "function_plot")
    session.edit({"op": "delete_object", "objectId": plot_id})
    assert [item.type for item in session.project.scene.objects] == ["axes"]
    assert all(plot_id not in beat.targets for beat in session.project.scene.beats)
    session.undo()
    assert any(item.id == plot_id for item in session.project.scene.objects)


def test_deleting_a_transform_source_removes_the_destination() -> None:
    session = Session(empty_project())
    session.edit({"op": "add_formula_transform"})
    session.edit({"op": "delete_object", "objectId": "formula_1"})
    assert session.project.scene.objects == []
    assert session.project.scene.beats == []
    session.undo()
    assert len(session.project.scene.objects) == 2


def test_formula_anchor_and_shift_are_stored_per_formula() -> None:
    session = Session(empty_project())
    session.edit({"op": "add_formula_transform"})
    session.edit({"op": "set_object_shift", "objectId": "formula_1", "shift": [1, 0]})
    session.edit({"op": "set_anchor", "objectId": "formula_2", "anchor": "edge_down"})
    session.edit({"op": "set_object_shift", "objectId": "formula_2", "shift": [0, 2]})
    restored = load_text(dumps_project(session.project))
    source, dest = restored.scene.objects
    assert list(source.params["shift"]) == [1, 0]
    assert source.params["anchor"] == "edge_up"
    assert list(dest.params["shift"]) == [0, 2]
    assert dest.params["anchor"] == "edge_down"
    instance = restored.scene.template_instances[0]
    assert instance.params["sourceAnchor"] == "edge_up"
    assert list(instance.params["sourceShift"]) == [1, 0]
    assert instance.params["destinationAnchor"] == "edge_down"
    assert list(instance.params["destinationShift"]) == [0, 2]


def test_windows_job_objects_are_the_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("MATHIMATOR_WIN32_JOBS", raising=False)
    assert jobs_enabled() is (os.name == "nt")
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "0")
    assert jobs_enabled() is False


def test_object_ids_reject_keywords_and_generated_names() -> None:
    session = Session(empty_project())
    session.edit({"op": "add_formula"})
    text = dumps_project(session.project)
    for bad in ("class", "np"):
        broken = text.replace("formula_1", bad)
        try:
            load_text(broken)
        except DocumentError as exc:
            assert "not a valid generated name" in str(exc)
        else:
            raise AssertionError(f"{bad} was accepted")


def test_stop_joins_for_one_second(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    supervisor = Supervisor(tmp_path / "join", sys.executable, tmp_path / "join.log", True)
    thread = threading.Thread(target=time.sleep, args=(3,), daemon=True)
    thread.start()
    job = _Job(job_id="preview-1", profile="preview", thread=thread, cancel=threading.Event())
    started = time.perf_counter()
    assert supervisor._stop(job) is False
    assert 0.9 < time.perf_counter() - started < 1.5
    assert job.cancel.is_set()


def test_clear_cache_keeps_project_and_exports(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    project = tmp_path / "cache-project"
    log_path = tmp_path / "cache.log"
    client = start_supervisor(project, log_path, init=True)
    try:
        client.send({"cmd": "status", "id": "status"})
        client.wait_for(lambda event: event.get("event") == "ready" and event.get("jobId") == "status", 10)
        client.send({"cmd": "edit", "id": "add", "edit": {"op": "add_formula"}})
        added = client.wait_for(lambda event: event.get("jobId") == "add", 10)
        assert added["event"] == "state"
        export = project / ".mathimator" / "exports" / "keep.txt"
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text("keep", encoding="utf-8")
        stale = project / ".mathimator" / "cache" / "stale.txt"
        stale.write_text("stale", encoding="utf-8")
        client.send({"cmd": "clear_cache", "id": "clear"})
        cleared = client.wait_for(lambda event: event.get("jobId") == "clear", 10)
        assert cleared["event"] == "state"
        assert (project / "project.json").is_file()
        assert export.read_text(encoding="utf-8") == "keep"
        assert not stale.exists()
    finally:
        client.close()


def test_queued_preview_starts_once_after_export(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "0")
    fake = write_fake_interpreter(tmp_path / "fake-manim.py", 0.6)
    project = tmp_path / "handoff"
    log_path = tmp_path / "handoff.log"
    client = start_supervisor(project, log_path, python=fake, init=True)
    try:
        client.send({"cmd": "status", "id": "status"})
        client.wait_for(lambda event: event.get("event") == "ready" and event.get("jobId") == "status", 10)
        client.send({"cmd": "export", "id": "export-1"})
        read_argv(log_path, timeout=10)
        client.send({"cmd": "render_preview", "id": "preview-1"})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _argv_count(log_path) < 2:
            time.sleep(0.05)
        time.sleep(0.5)
        assert _argv_count(log_path) == 2
    finally:
        client.close()


def test_a_wait_fails_as_soon_as_the_supervisor_dies_and_shows_its_stderr() -> None:
    """A supervisor that crashes fails the test at once, with its traceback, not at the timeout (F145)."""
    crash = "import sys; sys.stderr.write('Traceback (most recent call last):\\nRuntimeError: boom\\n'); sys.exit(3)"
    pipes = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    client = SupervisorClient(subprocess.Popen([sys.executable, "-c", crash], **pipes))
    started = time.monotonic()
    with pytest.raises(AssertionError, match="the supervisor closed its stdout; exit code 3") as failed:
        client.wait_for_end("preview-1", 60)
    assert time.monotonic() - started < 10
    assert "RuntimeError: boom" in str(failed.value)
    client.close()


_PARTIALS = "partial_movie_files/MainScene"
# A quality folder, relative to the project folder of a discard.
_VIDEO = "videos/scene/480p15"
_CUT_OFF = b"\x00" * 48


@pytest.mark.parametrize(
    ("relative", "data", "earlier", "discarded"),
    [
        pytest.param(f"{_PARTIALS}/{'b' * 64}.mp4", _CUT_OFF, False, True, id="cut-off-partial"),
        pytest.param(f"{_PARTIALS}/{'c' * 64}.mp4", complete_clip(".mp4"), False, False, id="complete-partial"),
        pytest.param(f"{_PARTIALS}/{'a' * 64}.mp4", _CUT_OFF, True, False, id="earlier-partial"),
        pytest.param("MainScene.gif", b"GIF89a", False, True, id="movie"),
        pytest.param("MainScene.mp4", complete_clip(".mp4"), True, False, id="earlier-movie"),
        pytest.param(f"{_PARTIALS}/partial_movie_file_list.txt", b"", False, True, id="segment-list"),
        pytest.param("notes.txt", b"", False, False, id="not-media"),
    ],
)
def test_a_discard_takes_only_what_the_run_wrote_that_may_be_cut_off(
    tmp_path: Path, relative: str, data: bytes, earlier: bool, discarded: bool
) -> None:
    """Each rule of the discard on its own, so a broken one cannot hide behind another (F140).

    A cancel, a failure and the interrupted-run sweep all discard this way (G5.10).
    """
    video = tmp_path / "videos" / "scene" / "480p15"
    path = video / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    before = run_files(video) if earlier else {}
    assert discard_written(tmp_path, video, before, ClipChecker()) == (([path], []) if discarded else ([], []))
    assert path.exists() is not discarded


@pytest.mark.parametrize(
    "linked",
    [
        pytest.param("videos", id="above-the-quality-folder"),
        pytest.param(_VIDEO, id="quality-folder"),
        pytest.param(f"{_VIDEO}/partial_movie_files", id="partial-movie-files"),
        pytest.param(f"{_VIDEO}/{_PARTIALS}", id="partials"),
    ],
)
def test_a_discard_never_deletes_through_a_link(tmp_path: Path, linked: str, link_kind: str) -> None:
    """A link planted anywhere in the project never lets a discard delete outside it (O10)."""
    project, outside = tmp_path / "project", tmp_path / "outside"
    partials = outside / _VIDEO / _PARTIALS
    partials.mkdir(parents=True)
    foreign = [partials / f"{'b' * 64}.mp4", partials / MANIFEST, outside / _VIDEO / "MainScene.mp4"]
    for path in foreign:
        path.write_bytes(_CUT_OFF)
    link = project / linked
    link.parent.mkdir(parents=True)
    link_folder(link, outside / linked, link_kind)
    video = project / _VIDEO
    # The movie of a run is still discarded when only its partials folder is linked.
    movie = video / "MainScene.mp4"
    below = video in link.parents
    if below:
        movie.write_bytes(_CUT_OFF)
    assert discard_written(project, video, {}, ClipChecker()) == ([movie] if below else [], [])
    assert all(path.is_file() for path in foreign)


def test_the_run_marker_keeps_stamps_relative_to_the_quality_folder(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "scene" / "480p15"
    partial = video / _PARTIALS / ("a" * 64 + ".mp4")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(complete_clip(".mp4"))
    stamps = run_files(video)
    record = json.loads(json.dumps(stamps_record(video, stamps)))
    # The project was moved or copied before the next bind.
    moved = tmp_path / "moved"
    assert read_stamps(moved, record) == {moved / _PARTIALS / partial.name: stamps[partial]}
    for broken in ([1, 2], {"MainScene.mp4": [1]}, {"MainScene.mp4": "1 2"}, {"MainScene.mp4": [1.5, 2]}):
        assert read_stamps(video, broken) is None


def _argv_count(log_path: Path) -> int:
    if not log_path.is_file():
        return 0
    return log_path.read_text(encoding="utf-8", errors="replace").count("argv: ")
