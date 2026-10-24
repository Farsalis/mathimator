"""Follow-up fixes that do not call Win32.

The Job Object tests live in ``test_win32_job.py`` and stay skipped unless
``MATHIMATOR_WIN32_TESTS=1``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mathimator_worker.document import dumps_project, empty_project, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.progress import discard_unfinished_partials
from mathimator_worker.session import Session
from mathimator_worker.supervisor import Supervisor, _Job
from mathimator_worker.win32_job import jobs_enabled
from tests.support import SupervisorClient, read_argv, start_supervisor, write_fake_interpreter


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


@pytest.mark.parametrize(
    ("relative", "age", "swept"),
    [
        pytest.param(f"partial_movie_files/MainScene/{'b' * 64}.mp4", 0, True, id="partial-of-the-run"),
        pytest.param(f"partial_movie_files/MainScene/{'a' * 64}.mp4", 30, False, id="older-partial"),
        pytest.param("MainScene.mp4", 0, True, id="movie-of-the-run"),
        pytest.param("partial_movie_files/MainScene/partial_movie_file_list.txt", 0, True, id="segment-list"),
        pytest.param("notes.txt", 0, False, id="not-media"),
    ],
)
def test_the_sweep_takes_only_media_written_since_the_run_started(
    tmp_path: Path, relative: str, age: float, swept: bool
) -> None:
    """Each rule of the interrupted-run sweep on its own, so a broken one cannot hide behind another (F140)."""
    media = tmp_path / "media"
    path = media / "videos" / "scene" / "480p15" / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00" * 48)
    written = time.time() - age
    os.utime(path, (written, written))
    removed = discard_unfinished_partials(media, time.time() - 2)
    assert (removed, path.exists()) == (([path], False) if swept else ([], True))


def _argv_count(log_path: Path) -> int:
    if not log_path.is_file():
        return 0
    return log_path.read_text(encoding="utf-8", errors="replace").count("argv: ")
