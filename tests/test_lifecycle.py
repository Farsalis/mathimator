"""The project lifecycle: lock, fingerprint, folder identity, links, the project commands, and the log (C8, C10).

Every test runs Supervisors in this process through ``handle_line`` and reads
their replies from the captured stdout. Jobs run the ``fake_manim`` stand-in.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from mathimator_worker import process_group
from mathimator_worker import supervisor as supervisor_module
from mathimator_worker.document import dumps_project, empty_project, load_text
from mathimator_worker.identity import beat_chain_keys
from mathimator_worker.project_folder import ProjectLock, replace
from mathimator_worker.session import Session
from mathimator_worker.supervisor import (
    FILES_IN_USE,
    PROJECT_CHANGED,
    PROJECT_LOCKED,
    PROJECT_UNAVAILABLE,
    Supervisor,
    _Job,
)
from tests.support import (
    REPO,
    FakeManim,
    Stdout,
    complete_clip,
    link_file,
    link_folder,
    unreadable,
    video_render,
    write_clip,
)

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"

Start = Callable[..., Supervisor]


@pytest.fixture
def start(monkeypatch: pytest.MonkeyPatch) -> Iterator[Start]:
    """Make Supervisors that are closed, so their locks are released, when the test ends."""
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    made: list[Supervisor] = []

    def make(project: Path, *, init: bool = True, log: bool = True) -> Supervisor:
        log_path = project.parent / f"{project.name}.log" if log else None
        supervisor = Supervisor(project, sys.executable, log_path, init)
        made.append(supervisor)
        return supervisor

    yield make
    for supervisor in made:
        supervisor.close()


# The project lock (D58)


def test_a_locked_project_is_refused_until_its_worker_exits(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    folder = _journey(tmp_path / "shared")
    first = start(folder)
    second = start(folder, init=False)
    _send(second, cmd="status", id="status")
    _send(second, cmd="open_project", id="open", path=str(folder))
    first.close()
    _send(second, cmd="open_project", id="again", path=str(folder))
    status, refused, opened = stdout.read()
    assert status["startupError"] == {"code": "project_locked", "message": PROJECT_LOCKED}
    assert refused == {"event": "rejected", "jobId": "open", "code": "project_locked", "message": PROJECT_LOCKED}
    assert (opened["event"], opened["projectPath"]) == ("state", str(folder.resolve()))


def test_a_folder_that_cannot_hold_the_lock_is_lock_failed(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "bound"))
    blocked = _journey(tmp_path / "blocked")
    (blocked / ".mathimator").write_text("not a folder", encoding="utf-8")
    _send(supervisor, cmd="open_project", id="open", path=str(blocked))
    _send(supervisor, cmd="get_state", id="state")
    refused, state = stdout.read()
    assert refused["code"] == "lock_failed"
    assert refused["message"].startswith(f"Mathimator cannot write to {blocked.resolve()}: ")
    assert (blocked / ".mathimator").read_text(encoding="utf-8") == "not a folder"
    assert state["projectPath"] == str(supervisor.project)


def test_init_writes_only_behind_the_lock(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    held = _journey(tmp_path / "held")
    start(held)
    (held / "project.json").unlink()
    other = start(_journey(tmp_path / "other"))
    _send(other, cmd="open_project", id="open", path=str(held), init=True)
    late = start(held)
    _send(late, cmd="status", id="status")
    refused, status = stdout.read()
    assert refused["code"] == "project_locked"
    assert status["startupError"]["code"] == "project_locked"
    assert not (held / "project.json").exists()


# Folders that are links (O10)


@pytest.mark.parametrize(
    "linked",
    [
        ".mathimator",
        ".mathimator/cache",
        ".mathimator/cache/media",
        ".mathimator/cache/media/Tex",
        ".mathimator/cache/media/videos",
        ".mathimator/cache/media/images",
        ".mathimator/cache/last-good",
        ".mathimator/codegen",
        ".mathimator/exports",
        ".mathimator/equation",
        ".mathimator/logs",
    ],
)
def test_a_project_whose_own_folder_is_a_link_is_never_bound(
    tmp_path: Path, start: Start, stdout: Stdout, linked: str, link_kind: str
) -> None:
    supervisor = start(_journey(tmp_path / "bound"))
    shared = _journey(tmp_path / "shared")
    link = shared.resolve() / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link_folder(link, elsewhere, link_kind)
    _send(supervisor, cmd="open_project", id="open", path=str(shared))
    _send(supervisor, cmd="get_state", id="state")
    _send(start(shared, init=False), cmd="status", id="status")
    refused, state, status = stdout.read()
    assert refused == {"event": "rejected", "jobId": "open", "code": "linked_folder", "message": _linked(link)}
    assert status["startupError"] == {"code": "linked_folder", "message": _linked(link)}
    assert state["projectPath"] == str(supervisor.project)
    # The refusal comes before the lock, so nothing is written there or through the link.
    assert not os.path.lexists(shared / ".mathimator" / "lock")
    assert list(elsewhere.iterdir()) == []


def test_a_log_that_is_a_link_is_refused_and_takes_no_line(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    mine = _made(tmp_path / "elsewhere" / "notes.txt")
    mine.write_bytes(b"mine")
    shared = _journey(tmp_path / "shared")
    log = _made(shared.resolve() / ".mathimator" / "logs" / "worker.log")
    link_file(log, mine)
    _send(start(shared, init=False, log=False), cmd="status", id="status")
    assert stdout.last()["startupError"] == {"code": "linked_folder", "message": _linked(log)}
    # A log that became a link after the bind takes no line, and Clear cache never empties what it points to.
    supervisor = start(_journey(tmp_path / "journey"), log=False)
    _send(supervisor, cmd="get_state", id="state")
    supervisor.log_path.unlink()
    link_file(supervisor.log_path, mine)
    _send(supervisor, cmd="clear_cache", id="clear")
    refused = stdout.last()
    assert (refused["code"], refused["message"]) == ("linked_folder", _linked(supervisor.log_path))
    assert mine.read_bytes() == b"mine"


def test_save_as_refuses_a_folder_whose_mathimator_is_a_link(
    tmp_path: Path, start: Start, stdout: Stdout, link_kind: str
) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    target = tmp_path / "target"
    target.mkdir()
    gone = tmp_path / "gone"
    gone.mkdir()
    link_folder(target / ".mathimator", gone, link_kind)
    # A link counts even when it leads nowhere.
    gone.rmdir()
    _send(supervisor, cmd="save_as", id="save", path=str(target))
    assert stdout.last() == {
        "event": "rejected",
        "jobId": "save",
        "code": "save_as_failed",
        "message": f"Could not save the project to {target}: it already holds .mathimator.",
    }
    assert [path.name for path in target.iterdir()] == [".mathimator"]


# open_project, reload, overwrite (C8.3)


def test_a_refused_open_changes_nothing(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "bound"))
    _send(supervisor, cmd="edit", id="rename", edit=_rename("Renamed"))
    refused_folder = tmp_path / "refused"
    refused_folder.mkdir()
    (refused_folder / "project.json").write_text("{", encoding="utf-8")
    _send(supervisor, cmd="open_project", id="open", path=str(refused_folder))
    _send(supervisor, cmd="get_state", id="state")
    _renamed, refused, state = stdout.read()
    assert refused["code"] == "invalid_json"
    assert state["projectPath"] == str(supervisor.project)
    assert (state["document"]["name"], state["derived"]["canUndo"]) == ("Renamed", True)
    assert [path.name for path in refused_folder.iterdir()] == ["project.json"]
    # The attempt released the folder's lock, so another worker can take it.
    ProjectLock.acquire(refused_folder).discard()


def test_open_project_with_init_fills_an_empty_folder(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "bound"))
    empty = tmp_path / "new"
    empty.mkdir()
    _send(supervisor, cmd="open_project", id="plain", path=str(empty))
    assert stdout.last()["code"] == "not_a_project"
    assert list(empty.iterdir()) == []
    _send(supervisor, cmd="open_project", id="init", path=str(empty), init=True)
    opened = stdout.last()
    assert (opened["event"], opened["projectPath"]) == ("state", str(empty.resolve()))
    assert (empty / "project.json").read_text(encoding="utf-8") == dumps_project(empty_project())
    assert (empty / ".mathimator" / "lock").is_file()


def test_a_failed_init_write_leaves_the_folder_as_it_was(
    tmp_path: Path, start: Start, stdout: Stdout, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = start(_journey(tmp_path / "bound"))
    empty = tmp_path / "new"
    empty.mkdir()

    def full(source: object, target: object) -> None:
        raise OSError(28, "There is not enough space on the disk")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", full)
        _send(supervisor, cmd="open_project", id="open", path=str(empty), init=True)
    assert stdout.last() == {
        "event": "rejected",
        "jobId": "open",
        "code": "save_failed",
        "message": "Could not save project.json: There is not enough space on the disk. The change was undone.",
    }
    assert list(empty.iterdir()) == []


def test_an_unreadable_project_json_is_read_failed(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "bound"))
    other = _journey(tmp_path / "other")
    with unreadable(other / "project.json"), unreadable(supervisor.layout.project_file):
        _send(supervisor, cmd="open_project", id="open", path=str(other))
        _send(supervisor, cmd="reload", id="reload")
        _send(supervisor, cmd="edit", id="edit", edit=_rename("Renamed"))
    for refused in stdout.read():
        assert refused["code"] == "read_failed"
        assert refused["message"].startswith("Could not read project.json: ")
    assert [path.name for path in other.iterdir()] == ["project.json"]


def test_an_outside_change_is_refused_until_overwrite_or_reload(
    tmp_path: Path, start: Start, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    _send(supervisor, cmd="edit", id="first", edit=_rename("First"))
    outside = JOURNEY.read_text(encoding="utf-8").replace("Sine introduction", "Edited elsewhere")
    supervisor.layout.project_file.write_text(outside, encoding="utf-8", newline="\n")
    _send(supervisor, cmd="edit", id="second", edit=_rename("Second"))
    _send(supervisor, cmd="undo", id="undo")
    _run(supervisor, cmd="render_preview", id="preview-1")
    for job_id in ("second", "undo", "preview-1"):
        (refused,) = stdout.of(job_id)
        assert refused == {"event": "rejected", "jobId": job_id, "code": "project_changed", "message": PROJECT_CHANGED}
    assert supervisor.layout.project_file.read_text(encoding="utf-8") == outside
    assert not fake_manim.ran()

    _send(supervisor, cmd="overwrite", id="overwrite")
    assert (stdout.last()["event"], _saved_name(supervisor)) == ("state", "First")
    _send(supervisor, cmd="edit", id="third", edit=_rename("Third"))
    assert (stdout.last()["changed"], _saved_name(supervisor)) == (True, "Third")

    supervisor.layout.project_file.write_text(outside, encoding="utf-8", newline="\n")
    _send(supervisor, cmd="reload", id="reload")
    reloaded = stdout.last()
    assert reloaded["document"]["name"] == "Edited elsewhere"
    assert (reloaded["derived"]["canUndo"], reloaded["derived"]["canRedo"]) == (False, False)
    _send(supervisor, cmd="edit", id="fourth", edit=_rename("Fourth"))
    assert (stdout.last()["event"], _saved_name(supervisor)) == ("state", "Fourth")


def test_a_moved_folder_refuses_the_change_and_keeps_the_earlier_ones(
    tmp_path: Path, start: Start, stdout: Stdout, fake_manim: FakeManim
) -> None:
    folder = _journey(tmp_path / "lesson")
    # The log lives in the folder, so logging after the move must not bring the folder back (G1.3).
    supervisor = start(folder, log=False)
    _send(supervisor, cmd="edit", id="before", edit=_rename("Before the move"))
    # Windows refuses to rename a folder while a file in it is open, so the lock lets go first.
    _binding_lock(supervisor).release()
    folder.rename(tmp_path / "lesson final")
    _send(supervisor, cmd="edit", id="after", edit=_rename("After the move"))
    _send(supervisor, cmd="undo", id="undo")
    _send(supervisor, cmd="clear_cache", id="clear")
    _run(supervisor, cmd="render_preview", id="preview-1")
    for job_id in ("after", "undo", "clear", "preview-1"):
        (refused,) = stdout.of(job_id)
        assert (refused["code"], refused["message"]) == ("project_unavailable", PROJECT_UNAVAILABLE)
    # An edit that changes nothing writes nothing, so it runs no check (C8.1).
    _send(supervisor, cmd="edit", id="same", edit=_rename("Before the move"))
    assert (stdout.last()["event"], stdout.last()["changed"]) == ("state", False)
    assert not folder.exists()
    assert not fake_manim.ran()

    # D57, C14 Q1: the edits saved before the move stay in memory, and Save As keeps them.
    rescue = tmp_path / "rescue"
    rescue.mkdir()
    _send(supervisor, cmd="save_as", id="save", path=str(rescue))
    saved = stdout.last()
    assert (saved["projectPath"], saved["document"]["name"]) == (str(rescue.resolve()), "Before the move")
    assert saved["derived"]["canUndo"] is True
    assert _saved_name(supervisor) == "Before the move"
    _send(supervisor, cmd="open_project", id="open", path=str(_journey(tmp_path / "other")))
    assert stdout.last()["event"] == "state"
    assert not folder.exists()


def test_a_folder_put_in_place_of_the_bound_one_is_not_written(
    tmp_path: Path, start: Start, stdout: Stdout, fake_manim: FakeManim
) -> None:
    folder = _journey(tmp_path / "lesson")
    supervisor = start(folder)
    _send(supervisor, cmd="edit", id="before", edit=_rename("Before the move"))
    _binding_lock(supervisor).release()
    moved = folder.rename(tmp_path / "lesson moved")
    # The same bytes at the same path: only the folder's identity tells it from the bound one (D57).
    replacement = _made(folder / "project.json")
    replacement.write_bytes((moved / "project.json").read_bytes())
    saved = replacement.read_bytes()
    _send(supervisor, cmd="edit", id="after", edit=_rename("After the move"))
    _send(supervisor, cmd="undo", id="undo")
    _run(supervisor, cmd="render_preview", id="preview-1")
    for job_id in ("after", "undo", "preview-1"):
        (refused,) = stdout.of(job_id)
        assert (refused["code"], refused["message"]) == ("project_unavailable", PROJECT_UNAVAILABLE)
    assert replacement.read_bytes() == saved
    assert [path.name for path in folder.iterdir()] == ["project.json"]
    assert not fake_manim.ran()


def test_a_deleted_project_json_is_not_written_again(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    supervisor.layout.project_file.unlink()
    _send(supervisor, cmd="edit", id="edit", edit=_rename("Renamed"))
    # Overwrite skips the fingerprint, but not the check that project.json is there (D57).
    _send(supervisor, cmd="overwrite", id="overwrite")
    _send(supervisor, cmd="reload", id="reload")
    for job_id in ("edit", "overwrite", "reload"):
        (refused,) = stdout.of(job_id)
        assert (refused["code"], refused["message"]) == ("project_unavailable", PROJECT_UNAVAILABLE)
    assert not supervisor.layout.project_file.exists()


def test_a_cache_record_that_cannot_be_written_does_not_refuse_the_edit(
    tmp_path: Path, start: Start, stdout: Stdout
) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    shutil.rmtree(supervisor.layout.rendered_path.parent)
    supervisor.layout.rendered_path.parent.write_text("in the way", encoding="utf-8")
    _send(supervisor, cmd="edit", id="rename", edit=_rename("Renamed"))
    reply = stdout.last()
    assert (reply["event"], reply["changed"], _saved_name(supervisor)) == ("state", True, "Renamed")
    assert f"could not write {supervisor.layout.rendered_path}" in supervisor.log_path.read_text(encoding="utf-8")


# Saving project.json (D05, C13.2 E3, G5.9)


def test_a_save_is_synced_before_it_replaces_project_json(
    tmp_path: Path, start: Start, stdout: Stdout, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    calls: list[str] = []
    fsync, real_replace = os.fsync, os.replace

    def synced(fd: int) -> None:
        calls.append("fsync")
        fsync(fd)

    def replaced(source: str, target: str) -> None:
        calls.append(f"replace {Path(source).name} -> {Path(target).name}")
        real_replace(source, target)

    monkeypatch.setattr(os, "fsync", synced)
    monkeypatch.setattr(os, "replace", replaced)
    _send(supervisor, cmd="edit", id="rename", edit=_rename("Renamed"))
    assert stdout.last()["event"] == "state"
    assert calls[0] == "fsync"
    assert calls[1].startswith("replace .project.json.") and calls[1].endswith(".tmp -> project.json")
    assert sorted(path.name for path in supervisor.project.iterdir()) == [".mathimator", "project.json"]


@pytest.mark.parametrize(("winerror", "attempts"), [(32, 3), (5, 3), (None, 1)], ids=["in-use", "denied", "other"])
def test_replace_waits_only_while_windows_reports_the_file_in_use(
    winerror: int | None, attempts: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "new.json"
    source.write_text("new", encoding="utf-8")
    target = tmp_path / "project.json"
    target.write_text("old", encoding="utf-8")
    calls: list[Path] = []
    real_replace = os.replace

    def busy(first: Path, second: Path) -> None:
        calls.append(second)
        if len(calls) < 3:
            refused = PermissionError(13, "The process cannot access the file")
            refused.winerror = winerror
            raise refused
        real_replace(first, second)

    monkeypatch.setattr(os, "replace", busy)
    with pytest.raises(PermissionError) if winerror is None else contextlib.nullcontext():
        replace(source, target)
    assert len(calls) == attempts
    assert target.read_text(encoding="utf-8") == ("old" if winerror is None else "new")


def test_undo_and_redo_answer_within_their_budget(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    # Spec section 2 gives undo 50 ms, and every project.json write is synced (C13.2 E3).
    supervisor = start(_journey(tmp_path / "journey"))
    _send(supervisor, cmd="edit", id="rename", edit=_rename("Renamed"))
    times: list[float] = []
    for number in range(10):
        started = time.perf_counter()
        _send(supervisor, cmd="redo" if number % 2 else "undo", id=f"history-{number}")
        times.append(time.perf_counter() - started)
    assert [line["event"] for line in stdout.read()] == ["state"] * 11
    assert statistics.median(times) < 0.05, times


# Edits that change nothing (D08, C10)


def test_an_edit_that_changes_nothing_is_not_recorded(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    _send(supervisor, cmd="edit", id="red", edit=_color("#FF0000"))
    _send(supervisor, cmd="undo", id="undo")
    saved = supervisor.layout.project_file.read_bytes()
    stamp = supervisor.layout.project_file.stat().st_mtime_ns
    current = {"format": "mp4", "width": 1920, "height": 1080, "fps": 30, "transparent": False}
    _send(supervisor, cmd="edit", id="same-profile", edit={"op": "set_export_profile", **current})
    unchanged = stdout.last()
    assert (unchanged["changed"], unchanged["derived"]["canUndo"], unchanged["derived"]["canRedo"]) == (
        False,
        False,
        True,
    )
    assert (supervisor.layout.project_file.read_bytes(), supervisor.layout.project_file.stat().st_mtime_ns) == (
        saved,
        stamp,
    )

    gif = {"format": "gif", "width": 854, "height": 480, "fps": 15, "transparent": False}
    _send(supervisor, cmd="edit", id="gif", edit={"op": "set_export_profile", **gif})
    changed = stdout.last()
    assert (changed["changed"], changed["derived"]["canUndo"], changed["derived"]["canRedo"]) == (True, True, False)
    assert _undo_depth(supervisor) == 1

    _send(supervisor, cmd="edit", id="red", edit=_color("#FF0000"))
    _send(supervisor, cmd="edit", id="lowercase", edit=_color("#ff0000"))
    assert (stdout.last()["changed"], _undo_depth(supervisor)) == (False, 2)
    _send(supervisor, cmd="undo", id="undo-red")
    _send(supervisor, cmd="undo", id="undo-gif")
    assert stdout.last()["document"]["exportProfile"] == current


def test_an_edit_that_changes_nothing_skips_the_disk_checks(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    outside = JOURNEY.read_text(encoding="utf-8").replace("Sine introduction", "Edited elsewhere")
    supervisor.layout.project_file.write_text(outside, encoding="utf-8", newline="\n")
    _send(supervisor, cmd="edit", id="same", edit=_rename("Sine introduction"))
    unchanged = stdout.last()
    assert (unchanged["event"], unchanged["changed"]) == ("state", False)
    assert supervisor.layout.project_file.read_text(encoding="utf-8") == outside


# Startup (C8.2)


def test_startup_on_a_missing_folder_creates_nothing(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    missing = tmp_path / "gone"
    supervisor = start(missing, log=False)
    supervisor.log("a line with no folder to hold it")
    _send(supervisor, cmd="status", id="status")
    _send(supervisor, cmd="get_state", id="state")
    other = _journey(tmp_path / "other")
    _send(supervisor, cmd="open_project", id="open", path=str(other))
    _send(supervisor, cmd="status", id="after")
    status, refused, opened, after = stdout.read()
    assert not missing.exists()
    assert status["startupError"] == {"code": "project_unavailable", "message": PROJECT_UNAVAILABLE}
    assert refused["message"] == f"No project is open. {PROJECT_UNAVAILABLE}"
    assert (opened["event"], after["startupError"]) == ("state", None)
    assert (other / ".mathimator" / "logs" / "worker.log").is_file()


def test_startup_with_init_fills_an_existing_folder(tmp_path: Path, start: Start) -> None:
    folder = tmp_path / "new"
    folder.mkdir()
    supervisor = start(folder)
    assert supervisor.session is not None
    assert (folder / "project.json").read_text(encoding="utf-8") == dumps_project(empty_project())
    assert sorted(path.name for path in (folder / ".mathimator").iterdir()) == ["cache", "lock"]


def test_startup_starts_no_helper_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATHIMATOR_SKIP_PROCESS_TABLE", raising=False)
    monkeypatch.setattr(process_group, "_TABLE", process_group._WindowsProcessTable())
    started: list[object] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: started.append(args))
    Supervisor(_journey(tmp_path / "journey"), sys.executable, tmp_path / "journey.log", True).close()
    assert started == []


# Save As (D29, C8.3)


def test_save_as_moves_a_rendered_project(tmp_path: Path, start: Start, stdout: Stdout, fake_manim: FakeManim) -> None:
    source = _journey(tmp_path / "temp")
    supervisor = start(source)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, cmd="render_preview", id="preview-1")
    _send(supervisor, cmd="edit", id="rename", edit=_rename("Saved"))
    state = source / ".mathimator"
    (state / "temp-project.json").write_text("{}", encoding="utf-8")
    export = state / "exports" / "key" / "MainScene.mp4"
    export.parent.mkdir(parents=True)
    write_clip(export)
    kept = sorted(path.relative_to(source) for path in source.rglob("*") if path.is_file())
    target = tmp_path / "kept"
    target.mkdir()
    _send(supervisor, cmd="save_as", id="save", path=str(target))
    saved = stdout.last()

    root = target.resolve()
    assert saved["projectPath"] == str(root)
    assert saved["derived"]["canUndo"] is True
    paths = [saved["index"]["output"], *(beat["partial"] for beat in saved["index"]["beats"])]
    assert len(paths) == 5
    for path in paths:
        assert Path(path).is_relative_to(root) and Path(path).is_file()
    assert sorted(path.name for path in (target / ".mathimator").iterdir()) == ["cache", "exports", "lock"]
    assert (target / ".mathimator" / "exports" / "key" / "MainScene.mp4").read_bytes() == export.read_bytes()
    stored = json.loads((target / ".mathimator" / "cache" / "preview-index.json").read_text(encoding="utf-8"))
    assert all(beat["partial"].startswith(".mathimator/cache/media/") for beat in stored["beats"])
    assert _saved_name(supervisor) == "Saved"
    # The old folder is unchanged and no longer locked.
    assert sorted(path.relative_to(source) for path in source.rglob("*") if path.is_file()) == kept
    ProjectLock.acquire(source).release()
    _send(supervisor, cmd="undo", id="undo")
    assert (stdout.last()["event"], _saved_name(supervisor)) == ("state", "Sine introduction")


def test_save_as_refuses_a_folder_that_cannot_take_the_project(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    holds_project = _journey(tmp_path / "holds-project")
    holds_state = tmp_path / "holds-state"
    (holds_state / ".mathimator").mkdir(parents=True)
    refusals = [
        (tmp_path / "missing", "it is not a folder"),
        (supervisor.project, "it is the folder of the open project"),
        (holds_project, "it already holds project.json"),
        (holds_state, "it already holds .mathimator"),
    ]
    for target, reason in refusals:
        _send(supervisor, cmd="save_as", id="save", path=str(target))
        assert stdout.last() == {
            "event": "rejected",
            "jobId": "save",
            "code": "save_as_failed",
            "message": f"Could not save the project to {target}: {reason}.",
        }
    assert not (tmp_path / "missing").exists()
    assert [path.name for path in holds_project.iterdir()] == ["project.json"]


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to copy a file that is open")
def test_save_as_says_why_the_cache_could_not_be_copied(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    held = write_clip(_made(supervisor.layout.media_dir / "held.mp4"))
    write_clip(supervisor.layout.media_dir / "loose.mp4")
    target = tmp_path / "copy-fails"
    target.mkdir()
    with unreadable(held):
        _send(supervisor, cmd="save_as", id="save", path=str(target))
    in_use = "[WinError 32] The process cannot access the file because it is being used by another process"
    assert stdout.last() == {
        "event": "rejected",
        "jobId": "save",
        "code": "save_as_failed",
        "message": f"Could not save the project to {target.resolve()}: {in_use}.",
    }
    assert list(target.iterdir()) == []
    _send(supervisor, cmd="edit", id="rename", edit=_rename("Still here"))
    assert (stdout.last()["projectPath"], _saved_name(supervisor)) == (str(supervisor.project), "Still here")


def test_open_and_save_as_wait_for_a_running_export(
    tmp_path: Path, start: Start, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    fake_manim.plan(video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4), hold=True)
    _send(supervisor, cmd="export", id="export-1")
    export = _current(supervisor)
    try:
        fake_manim.wait_until_held()
        target = tmp_path / "target"
        target.mkdir()
        _send(supervisor, cmd="save_as", id="save", path=str(target))
        _send(supervisor, cmd="open_project", id="open", path=str(_journey(tmp_path / "other")))
        _send(supervisor, cmd="clear_cache", id="clear")
    finally:
        fake_manim.release()
        _join(export)
    for job_id in ("save", "open"):
        assert stdout.of(job_id) == [
            {"event": "rejected", "jobId": job_id, "code": "export_running", "message": "An export is already running."}
        ]
    assert stdout.of("clear")[0]["code"] == "render_in_progress"
    assert list(target.iterdir()) == []
    assert [line["event"] for line in stdout.of("export-1")] == ["started", "done"]


# The preview index on disk (D18, F035, F092)


def test_a_copied_project_finds_its_own_previews(
    tmp_path: Path, start: Start, stdout: Stdout, fake_manim: FakeManim
) -> None:
    original = _journey(tmp_path / "lesson")
    supervisor = start(original)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, cmd="render_preview", id="preview-1")
    stored = json.loads(supervisor.layout.index_path.read_text(encoding="utf-8"))
    assert stored["output"] == ".mathimator/cache/media/videos/scene/480p15/MainScene.mp4"
    assert all(beat["partial"].startswith(".mathimator/cache/media/videos/scene/") for beat in stored["beats"])
    supervisor.close()
    copy = tmp_path / "lesson-v2"
    shutil.copytree(original, copy)
    _send(start(copy), cmd="get_state", id="state")
    index = stdout.last()["index"]
    assert [beat["fresh"] for beat in index["beats"]] == [True] * 4
    for path in [index["output"], *(beat["partial"] for beat in index["beats"])]:
        assert Path(path).is_relative_to(copy.resolve())


def test_a_stored_path_outside_the_project_is_dropped(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    project = _journey(tmp_path / "shared")
    session = Session(load_text(JOURNEY.read_text(encoding="utf-8")))
    session.mark_rendered("preview")
    cache = project / ".mathimator" / "cache"
    outside = write_clip(tmp_path / "secret.mp4")
    inside = write_clip(_made(cache / "media" / "beat.mp4"))
    stored = [str(outside), "../secret.mp4", ".mathimator/cache/media/beat.mp4", "//server/share/beat.mp4"]
    keys = beat_chain_keys(session.project, "preview")
    beats = [
        {
            "id": f"beat_{number}",
            "index": number - 1,
            "partial": path,
            "lastGood": path,
            "fresh": True,
            "partialKey": key,
            "lastGoodKey": key,
        }
        for number, (path, key) in enumerate(zip(stored, keys, strict=True), start=1)
    ]
    (cache / "preview-index.json").write_text(json.dumps({"output": str(outside), "beats": beats}), encoding="utf-8")
    (cache / "rendered.json").write_text(json.dumps(session.rendered_payload()), encoding="utf-8")
    _send(start(project), cmd="get_state", id="state")
    index = stdout.last()["index"]
    assert index["output"] is None
    assert [beat["partial"] for beat in index["beats"]] == [None, None, str(inside.resolve()), None]


# Clear cache (D55, D58, G5.8)


def test_clear_cache_keeps_the_project_files_and_empties_the_log(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"), log=False)
    state = supervisor.project / ".mathimator"
    for name in ("codegen/scene.py", "equation/formula.svg", "typst/old.svg", "logs/worker.log.1", "temp-project.json"):
        _made(state / name).write_text(name, encoding="utf-8")
    export = write_clip(_made(state / "exports" / "key" / "MainScene.mp4"))
    supervisor.log("a line from before")
    _send(supervisor, cmd="clear_cache", id="clear")
    cleared = stdout.last()
    assert (cleared["event"], "warning" in cleared) == ("state", False)
    assert sorted(path.name for path in state.iterdir()) == ["cache", "exports", "lock", "logs", "temp-project.json"]
    assert [path.name for path in (state / "logs").iterdir()] == ["worker.log"]
    assert export.is_file()
    log = supervisor.log_path.read_text(encoding="utf-8")
    assert "a line from before" not in log
    assert "event state jobId=clear" in log


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to delete a file that is open")
def test_clear_cache_reports_files_another_program_holds(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    held = write_clip(_made(supervisor.layout.media_dir / "held.mp4"))
    loose = write_clip(supervisor.layout.media_dir / "loose.mp4")
    with held.open("rb"):
        _send(supervisor, cmd="clear_cache", id="clear")
    cleared = stdout.last()
    assert (cleared["event"], cleared["warning"]) == ("state", {"code": "files_in_use", "message": FILES_IN_USE})
    assert (held.exists(), loose.exists()) == (True, False)
    assert (supervisor.project / ".mathimator" / "lock").is_file()
    assert f"could not remove {held}" in supervisor.log_path.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to delete a read-only file")
def test_clear_cache_removes_read_only_files(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"), log=False)
    state = supervisor.project / ".mathimator"
    # A copied or shared project folder can carry the read-only attribute (S26).
    read_only = [
        _made(state / "codegen" / "__pycache__" / "scene.cpython-314.pyc"),
        _made(state / "cache" / "media" / "videos" / "scene" / "480p15" / "MainScene.mp4"),
        _made(state / "logs" / "worker.log.1"),
    ]
    for path in read_only:
        path.write_bytes(b"old")
        path.chmod(stat.S_IREAD)
    (state / "codegen").chmod(stat.S_IREAD)
    _send(supervisor, cmd="clear_cache", id="clear")
    cleared = stdout.last()
    assert (cleared["event"], "warning" in cleared) == ("state", False)
    assert [path.exists() for path in read_only] == [False] * len(read_only)
    assert not (state / "codegen").exists()


def test_clear_cache_refuses_a_linked_folder_and_deletes_nothing(
    tmp_path: Path, start: Start, stdout: Stdout, link_kind: str
) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    elsewhere = tmp_path / "elsewhere"
    mine = write_clip(_made(elsewhere / "mine.mp4"))
    # A link made after the bind, where the codegen reset and Clear cache delete.
    codegen = supervisor.layout.scene_path.parent
    link_folder(codegen, elsewhere, link_kind)
    cached = write_clip(_made(supervisor.layout.media_dir / "cached.mp4"))
    _send(supervisor, cmd="clear_cache", id="clear")
    assert stdout.last() == {
        "event": "rejected",
        "jobId": "clear",
        "code": "linked_folder",
        "message": _linked(codegen),
    }
    assert (mine.is_file(), cached.is_file()) == (True, True)


def test_clear_cache_removes_links_and_never_what_they_point_to(
    tmp_path: Path, start: Start, stdout: Stdout, link_kind: str
) -> None:
    supervisor = start(_journey(tmp_path / "journey"), log=False)
    elsewhere = tmp_path / "elsewhere"
    mine = write_clip(_made(elsewhere / "mine.mp4"))
    state = supervisor.project / ".mathimator"
    links = [
        state / "typst",
        state / "logs" / "linked",
        supervisor.layout.media_dir / "texts",
        supervisor.layout.video_dir("480p15"),
    ]
    for link in links:
        link.parent.mkdir(parents=True, exist_ok=True)
        link_folder(link, elsewhere, link_kind)
    _send(supervisor, cmd="clear_cache", id="clear")
    cleared = stdout.last()
    assert (cleared["event"], "warning" in cleared) == ("state", False)
    assert [os.path.lexists(link) for link in links] == [False] * len(links)
    assert [path.name for path in elsewhere.iterdir()] == [mine.name]


# The worker log (D55)


def test_the_log_keeps_one_older_copy(tmp_path: Path, start: Start, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor_module, "LOG_LIMIT", 300)
    supervisor = start(_journey(tmp_path / "journey"), log=False)
    for number in range(20):
        supervisor.log(f"line {number:02d} {'x' * 40}")
    logs = supervisor.log_path.parent
    assert [path.name for path in sorted(logs.iterdir())] == ["worker.log", "worker.log.1"]
    newest = supervisor.log_path.read_text(encoding="utf-8")
    older = logs.joinpath("worker.log.1").read_text(encoding="utf-8")
    assert "line 19" in newest
    assert "line 00" not in newest + older
    assert len(older.encode("utf-8")) >= 300


def test_a_line_logged_while_a_bind_is_part_way_does_not_fail_the_bind(
    tmp_path: Path, start: Start, stdout: Stdout, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = _journey(tmp_path / "gone")
    supervisor = start(gone, log=False)
    _binding_lock(supervisor).release()
    shutil.rmtree(gone)
    sweep = supervisor._sweep_interrupted_run

    def logging_first() -> None:
        # Another thread, such as a preview that outlived its stop, logs while
        # the bind is part way, after the folder has changed.
        supervisor.log("a line from another thread")
        sweep()

    monkeypatch.setattr(supervisor, "_sweep_interrupted_run", logging_first)
    other = _journey(tmp_path / "other")
    _send(supervisor, cmd="open_project", id="open", path=str(other))
    assert (stdout.last()["event"], stdout.last()["projectPath"]) == ("state", str(other.resolve()))
    assert not gone.exists()


def test_state_and_code_lines_are_logged_by_size(tmp_path: Path, start: Start, stdout: Stdout) -> None:
    supervisor = start(_journey(tmp_path / "journey"))
    _send(supervisor, cmd="get_state", id="state")
    _send(supervisor, cmd="code", id="code")
    _send(supervisor, cmd="edit", id="bad", edit={"op": "set_latex", "objectId": "title_1", "latex": "\\input{x}"})
    state, code, refused = stdout.read()
    log = supervisor.log_path.read_text(encoding="utf-8")
    assert f"event state jobId=state bytes={_size(state)}\n" in log
    assert f"event code jobId=code bytes={_size(code)}\n" in log
    assert '"document"' not in log and '"source"' not in log
    assert f"event {json.dumps(refused, ensure_ascii=False)}\n" in log


def _journey(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "project.json").write_bytes(JOURNEY.read_bytes())
    return root


def _linked(link: Path) -> str:
    """The refusal of a project whose own folder or log ``link`` is a symbolic link or a junction (O10)."""
    return (
        f"Mathimator cannot use {link}: it is a link to another location, and Mathimator keeps its files inside "
        "the project. Delete the link and try again."
    )


def _made(path: Path) -> Path:
    """``path``, with its parent folders created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _send(supervisor: Supervisor, **request: object) -> None:
    supervisor.handle_line(json.dumps(request))


def _run(supervisor: Supervisor, **request: object) -> None:
    """Send a request and wait for the job it starts."""
    _send(supervisor, **request)
    _join(_current(supervisor))


def _current(supervisor: Supervisor) -> _Job:
    job = supervisor._current
    assert job is not None
    return job


def _join(job: _Job) -> None:
    job.thread.join(timeout=60)
    assert not job.thread.is_alive()


def _binding_lock(supervisor: Supervisor) -> ProjectLock:
    assert supervisor._binding is not None
    return supervisor._binding.lock


def _undo_depth(supervisor: Supervisor) -> int:
    assert supervisor.session is not None
    return len(supervisor.session._undo)


def _saved_name(supervisor: Supervisor) -> str:
    name: str = json.loads(supervisor.layout.project_file.read_text(encoding="utf-8"))["name"]
    return name


def _rename(name: str) -> dict[str, object]:
    return {"op": "set_project_name", "name": name}


def _color(color: str) -> dict[str, object]:
    return {"op": "set_object_color", "objectId": "plot_1", "color": color}


def _size(line: dict[str, Any]) -> int:
    """The bytes of ``line`` as the worker wrote it."""
    return len(json.dumps(line, ensure_ascii=False, allow_nan=False).encode("utf-8"))
