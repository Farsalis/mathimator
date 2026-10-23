"""Follow-up fixes that do not call Win32.

The Job Object tests live in ``test_win32_job.py`` and stay skipped unless
``MATHIMATOR_WIN32_TESTS=1``.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from mathimator_worker.document import dumps_project, empty_project, load_text
from mathimator_worker.document_error import DocumentError
from mathimator_worker.progress import discard_unfinished_partials
from mathimator_worker.session import Session
from mathimator_worker.supervisor import Supervisor
from mathimator_worker.tex_failure import format_render_failure
from mathimator_worker.win32_job import jobs_enabled
from tests.support import read_argv, start_supervisor, write_fake_interpreter


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


def test_latex_failure_includes_the_tex_log_line() -> None:
    tail = "ValueError: latex error converting to dvi\n! LaTeX Error: File `qrcode.sty' not found.\n"
    message = format_render_failure(1, tail)
    assert message.startswith("LaTeX failed.\n")
    assert "qrcode.sty" in message
    assert "manim exited with status" not in message


def test_other_manim_failures_keep_the_status(tmp_path: Path) -> None:
    del tmp_path
    message = format_render_failure(2, "RuntimeError: boom")
    assert message.startswith("manim exited with status 2")
    assert "boom" in message


def test_stop_process_joins_for_one_second(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    supervisor = Supervisor(tmp_path / "join", sys.executable, tmp_path / "join.log", True)
    thread = threading.Thread(target=time.sleep, args=(3,), daemon=True)
    thread.start()
    started = time.perf_counter()
    assert supervisor._stop_process(None, thread, "job") is False
    assert time.perf_counter() - started < 1.5


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


def test_cancelled_render_drops_the_unfinished_partial(tmp_path: Path) -> None:
    media = tmp_path / "media"
    partial_dir = media / "videos" / "scene" / "480p15" / "partial_movie_files" / "MainScene"
    partial_dir.mkdir(parents=True)
    good = partial_dir / ("a" * 64 + ".mp4")
    good.write_bytes(b"ok" * 100)
    old = time.time() - 30
    os.utime(good, (old, old))
    stub = partial_dir / ("b" * 64 + ".mp4")
    stub.write_bytes(b"\x00" * 48)
    movie = media / "videos" / "scene" / "480p15" / "MainScene.mp4"
    movie.parent.mkdir(parents=True, exist_ok=True)
    movie.write_bytes(b"\x00" * 48)
    manifest = media / "videos" / "scene" / "480p15" / "partial_movie_file_list.txt"
    manifest.write_text("file 'stub'\n", encoding="utf-8")
    removed = discard_unfinished_partials(media, time.time() - 2, {good})
    assert good.is_file()
    assert not stub.exists()
    assert not movie.exists()
    assert not manifest.exists()
    assert {path.name for path in removed} == {stub.name, movie.name, manifest.name}


def _argv_count(log_path: Path) -> int:
    if not log_path.is_file():
        return 0
    return log_path.read_text(encoding="utf-8", errors="replace").count("argv: ")
