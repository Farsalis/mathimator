"""Protocol 2: the request envelope, replies, cancel and hand-written mode (contract C2 to C7).

Every test runs a Supervisor in this process through ``handle_line`` and reads
its replies from the captured stdout. Each line is parsed the strict way the
shell parses it, so a line with NaN or any other non-JSON fails the test. Jobs
run the ``fake_manim`` stand-in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from mathimator_worker import process_group
from mathimator_worker import supervisor as supervisor_module
from mathimator_worker.document import NOT_UTF8
from mathimator_worker.jsonio import loads
from mathimator_worker.protocol import PROTOCOL_VERSION, REQUEST_FIELDS
from mathimator_worker.session import Session
from mathimator_worker.supervisor import NOT_A_PROJECT, Supervisor, _Job
from tests.support import REFERENCE, REPO, FakeManim, complete_clip, handwritten_project, video_render

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"
SHARED_COMMANDS = REPO / "tests" / "fixtures" / "protocol" / "commands.json"
NO_PROJECT = f"No project is open. {NOT_A_PROJECT}"


class Stdout:
    """Every line the supervisor wrote, each parsed as strict JSON."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self._capsys = capsys
        self.lines: list[dict[str, Any]] = []

    def read(self) -> list[dict[str, Any]]:
        for text in self._capsys.readouterr().out.splitlines():
            line = loads(text)
            assert isinstance(line, dict), text
            self.lines.append(line)
        return self.lines

    def last(self) -> dict[str, Any]:
        return self.read()[-1]

    def of(self, job_id: str) -> list[dict[str, Any]]:
        return [line for line in self.read() if line["jobId"] == job_id]

    def wait_for(self, job_id: str, event: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while True:
            found = [line for line in self.of(job_id) if line["event"] == event]
            if found:
                return found[0]
            assert time.monotonic() < deadline, self.lines
            time.sleep(0.02)


@pytest.fixture
def stdout(capsys: pytest.CaptureFixture[str]) -> Stdout:
    return Stdout(capsys)


@pytest.fixture
def quick_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill the fake Manim directly, so a cancel without a Job Object needs no process table helper."""
    monkeypatch.setattr(supervisor_module, "terminate_process_group", lambda proc: proc.kill())


# The envelope (C2, C4)


def test_status_reports_protocol_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout) -> None:
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    _send(supervisor, cmd="status", id="status-1", unknown="ignored")
    assert PROTOCOL_VERSION == 2
    assert stdout.read() == [{"event": "ready", "jobId": "status-1", "protocol": 2, "startupError": None}]


@pytest.mark.parametrize(
    ("line", "job_id"),
    [
        pytest.param('{"cmd": "status"', None, id="invalid-json"),
        pytest.param(
            '{"cmd":"edit","id":"e","edit":{"op":"set_domain","objectId":"plot_1","xMin":NaN,"xMax":2}}',
            None,
            id="nan",
        ),
        pytest.param('{"cmd": "status", "id": "r", "pad": -Infinity}', None, id="infinity"),
        pytest.param('{"cmd": "status", "id": "r", "pad": 1e999}', None, id="number-too-large"),
        pytest.param(f'{{"cmd": "status", "id": "r", "pad": {"9" * 400}}}', None, id="integer-too-large"),
        pytest.param('["status"]', None, id="not-an-object"),
        pytest.param('{"cmd": "status"}', None, id="no-id"),
        pytest.param('{"cmd": "status", "id": ""}', None, id="empty-id"),
        pytest.param('{"cmd": "status", "id": 7}', None, id="id-not-a-string"),
        pytest.param('{"id": "r"}', "r", id="no-cmd"),
        pytest.param('{"cmd": "", "id": "r"}', "r", id="empty-cmd"),
        pytest.param('{"cmd": "scrub", "id": "r", "time": 9}', "r", id="scrub-is-removed"),
        pytest.param('{"cmd": "render_frame", "id": "r"}', "r", id="render-frame-was-never-built"),
        pytest.param('{"cmd": "edit", "id": "r"}', "r", id="edit-missing"),
        pytest.param('{"cmd": "edit", "id": "r", "edit": "set_project_name"}', "r", id="edit-not-an-object"),
        pytest.param('{"cmd": "open_project", "id": "r"}', "r", id="path-missing"),
        pytest.param('{"cmd": "open_project", "id": "r", "path": 5}', "r", id="path-not-a-string"),
        pytest.param('{"cmd": "open_project", "id": "r", "path": "x", "init": "yes"}', "r", id="init-not-a-boolean"),
        pytest.param('{"cmd": "export", "id": "r", "force": 1}', "r", id="force-not-a-boolean"),
        pytest.param('{"cmd": "cancel", "id": "r"}', "r", id="cancel-without-target"),
        pytest.param('{"cmd": "cancel", "id": "r", "jobId": ""}', "r", id="cancel-empty-target"),
        pytest.param('{"cmd": "cancel", "id": "r", "jobId": null}', "r", id="cancel-null-target"),
    ],
)
def test_a_malformed_request_is_a_bad_request(
    line: str, job_id: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    supervisor.handle_line(line)
    _send(supervisor, cmd="status", id="after")
    refused, ready = stdout.read()
    assert {key: refused[key] for key in ("event", "jobId", "code")} == {
        "event": "error",
        "jobId": job_id,
        "code": "bad_request",
    }
    assert refused["message"]
    assert ready["event"] == "ready"


@pytest.mark.parametrize("edit", [{"op": "no_such_op"}, {}, {"op": 5}])
def test_a_bad_edit_op_is_an_invalid_edit_refusal(
    edit: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    _send(supervisor, cmd="edit", id="e", edit=edit)
    assert stdout.read() == [
        {
            "event": "rejected",
            "jobId": "e",
            "code": "invalid_edit",
            "message": f"Unknown edit operation {edit.get('op')}.",
            "field": "op",
        }
    ]


def test_an_unexpected_failure_is_internal_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="rename", edit={"op": "set_project_name", "name": "Renamed"})
    saved = supervisor.project_file.read_bytes()

    def broken(*args: object) -> None:
        raise RuntimeError("handler bug")

    monkeypatch.setattr("mathimator_worker.session.apply_edit", broken)
    _send(supervisor, cmd="edit", id="e", edit={"op": "set_project_name", "name": "Again"})
    _send(supervisor, cmd="status", id="after")
    *_, failed, ready = stdout.read()
    assert failed == {
        "event": "error",
        "jobId": "e",
        "code": "internal",
        "message": "Internal error: RuntimeError: handler bug. Details are in worker.log.",
    }
    assert ready["event"] == "ready"
    assert _undo_depth(supervisor) == 1
    assert supervisor.project_file.read_bytes() == saved
    log = supervisor.log_path.read_text(encoding="utf-8")
    assert "edit e failed\nTraceback (most recent call last):" in log
    assert "RuntimeError: handler bug" in log


def test_a_change_that_cannot_be_saved_is_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="rename", edit={"op": "set_project_name", "name": "Renamed"})
    saved = supervisor.project_file.read_bytes()

    def locked(source: object, target: object) -> None:
        raise PermissionError(13, "The file is in use")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", locked)
        _send(supervisor, cmd="edit", id="edit", edit={"op": "set_project_name", "name": "Unsaved"})
        _send(supervisor, cmd="undo", id="undo")
    assert [(line["jobId"], line["code"]) for line in stdout.read()[1:]] == [("edit", "internal"), ("undo", "internal")]
    assert supervisor.session is not None
    assert supervisor.session.project.name == "Renamed"
    assert (_undo_depth(supervisor), supervisor.session.can_redo()) == (1, False)
    assert supervisor.project_file.read_bytes() == saved
    assert sorted(path.name for path in supervisor.project.iterdir()) == [".mathimator", "project.json"]
    _send(supervisor, cmd="undo", id="undo-again")
    assert stdout.last()["event"] == "state"
    assert json.loads(supervisor.project_file.read_text(encoding="utf-8"))["name"] == "Sine introduction"


def test_a_reply_json_cannot_hold_becomes_an_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    state_event = Session.state_event

    def with_nan(self: Session, job_id: str, project_path: str) -> dict[str, object]:
        event = state_event(self, job_id, project_path)
        event["derived"] = {"total": float("nan")}
        return event

    monkeypatch.setattr(Session, "state_event", with_nan)
    _send(supervisor, cmd="get_state", id="state")
    _send(supervisor, cmd="status", id="after")
    failed, ready = stdout.read()
    assert (failed["event"], failed["jobId"], failed["code"]) == ("error", "state", "internal")
    assert "not JSON compliant" in failed["message"]
    assert ready["event"] == "ready"
    assert "could not serialize a state line" in supervisor.log_path.read_text(encoding="utf-8")


def test_the_log_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    supervisor.log_path = tmp_path
    supervisor.log("a folder cannot take this line")
    assert "a folder cannot take this line" in capsys.readouterr().err


def test_the_worker_commands_match_the_shared_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = json.loads(SHARED_COMMANDS.read_text(encoding="utf-8"))
    commands = set(shared["workerCommands"])
    assert {entry["worker"] for entry in shared["tauriCommands"]} - {None} <= commands
    # The project lifecycle commands of C8.3 are not served yet, so they are bad requests until they are.
    assert commands - set(REQUEST_FIELDS) == {"reload", "overwrite", "save_as"}
    assert set(REQUEST_FIELDS) <= commands
    assert set(_supervisor(tmp_path / "project", monkeypatch)._commands) == set(REQUEST_FIELDS)


# The handshake and startup errors (C6)


def test_status_reports_a_refused_startup_project_until_a_project_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    project = tmp_path / "utf16"
    project.mkdir()
    (project / "project.json").write_bytes(JOURNEY.read_text(encoding="utf-8").encode("utf-16"))
    supervisor = _supervisor(project, monkeypatch, init=False)
    _send(supervisor, cmd="status", id="status-1")
    _send(supervisor, cmd="get_state", id="state")
    _send(supervisor, cmd="open_project", id="open", path=str(_journey(tmp_path / "journey")))
    _send(supervisor, cmd="status", id="status-2")
    before, refused, opened, after = stdout.read()
    assert before == {
        "event": "ready",
        "jobId": "status-1",
        "protocol": 2,
        "startupError": {"code": "not_utf8", "message": NOT_UTF8},
    }
    assert refused == {
        "event": "rejected",
        "jobId": "state",
        "code": "no_project",
        "message": f"No project is open. {NOT_UTF8}",
    }
    assert opened["event"] == "state"
    assert after == {"event": "ready", "jobId": "status-2", "protocol": 2, "startupError": None}


def test_an_unreadable_startup_project_is_not_hand_written_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    project = tmp_path / "unreadable"
    (project / "project.json").mkdir(parents=True)
    supervisor = _supervisor(project, monkeypatch, init=False)
    _send(supervisor, cmd="status", id="status")
    assert stdout.last()["startupError"]["code"] == "read_failed"


@pytest.mark.parametrize("name", ["no-project-json", "missing", ""])
def test_open_project_refuses_a_folder_without_a_project(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    (tmp_path / "no-project-json").mkdir()
    _send(supervisor, cmd="open_project", id="open", path=str(tmp_path / name) if name else "")
    _send(supervisor, cmd="get_state", id="state")
    refused, state = stdout.read()
    assert refused == {"event": "rejected", "jobId": "open", "code": "not_a_project", "message": NOT_A_PROJECT}
    assert state["projectPath"] == str(supervisor.project)
    assert not (tmp_path / "no-project-json" / ".mathimator").exists()


def test_open_project_ignores_cache_records_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(tmp_path / "first", monkeypatch)
    project = _journey(tmp_path / "second")
    cache = project / ".mathimator" / "cache"
    cache.mkdir(parents=True)
    (cache / "preview-index.json").write_bytes(b"\xff\xfe{")
    (cache / "rendered.json").write_text("[" * 100_000, encoding="utf-8")
    _send(supervisor, cmd="open_project", id="open", path=str(project))
    opened = stdout.last()
    assert opened["event"] == "state"
    assert opened["projectPath"] == str(project.resolve())


def test_a_stored_clip_path_the_os_refuses_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    started = _with_index(_journey(tmp_path / "started"), "C:/x\u0000y.mp4")
    opened = _with_index(_journey(tmp_path / "opened"), "C:/x\u0000y.mp4")
    supervisor = _supervisor(started, monkeypatch, init=False)
    _send(supervisor, cmd="get_state", id="state")
    _send(supervisor, cmd="open_project", id="open", path=str(opened))
    for state in stdout.read():
        assert state["event"] == "state"
        assert [(beat["partial"], beat["lastGood"]) for beat in state["index"]["beats"]] == [(None, None)] * 4


def test_a_switch_that_fails_part_way_keeps_the_old_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    first = _journey(tmp_path / "first")
    supervisor = _supervisor(first, monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="rename", edit={"op": "set_project_name", "name": "Renamed"})

    def broken(self: Supervisor) -> None:
        raise RuntimeError("index bug")

    with monkeypatch.context() as patched:
        patched.setattr(Supervisor, "_rebuild_index_locked", broken)
        _send(supervisor, cmd="open_project", id="open", path=str(_journey(tmp_path / "second")))
    _send(supervisor, cmd="get_state", id="state")
    _send(supervisor, cmd="undo", id="undo")
    renamed, failed, state, undone = stdout.read()
    assert (failed["event"], failed["jobId"], failed["code"]) == ("error", "open", "internal")
    assert {**state, "jobId": "rename"} == renamed
    assert supervisor.project_file == first.resolve() / "project.json"
    assert (undone["event"], undone["document"]["name"]) == ("state", "Sine introduction")
    assert json.loads(supervisor.project_file.read_text(encoding="utf-8"))["name"] == "Sine introduction"


# Cancel (C7.3)


def test_cancel_without_a_matching_job_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-9")
    assert stdout.read() == [
        {
            "event": "rejected",
            "jobId": "cancel-1",
            "code": "no_such_job",
            "message": "No render with that id is running.",
        }
    ]


@pytest.mark.usefixtures("quick_kill")
def test_cancel_ends_a_running_job_with_one_cancelled_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    fake_manim.plan(video_render(supervisor.media_dir, "480p15", [complete_clip(".mp4")] * 4), hold=True)
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    stdout.wait_for("preview-1", "started")
    fake_manim.wait_until_held()
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
    _join(job)
    assert stdout.of("preview-1") == [
        {"event": "started", "jobId": "preview-1"},
        {"event": "error", "jobId": "preview-1", "code": "cancelled", "message": "cancelled"},
    ]
    assert stdout.of("cancel-1") == []
    _send(supervisor, cmd="cancel", id="cancel-2", jobId="preview-1")
    assert stdout.last() == {
        "event": "rejected",
        "jobId": "cancel-2",
        "code": "no_such_job",
        "message": "No render with that id is running.",
    }


@pytest.mark.usefixtures("quick_kill")
@pytest.mark.parametrize(
    ("command", "first_and_last"),
    [
        pytest.param("render_preview", ("started", "done"), id="newer-preview"),
        pytest.param("export", ("started", "done"), id="export"),
        pytest.param("open_project", ("state", "state"), id="open-project"),
    ],
)
def test_an_automatic_cancel_ends_the_preview_with_one_cancelled_line(
    command: str,
    first_and_last: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: Stdout,
    fake_manim: FakeManim,
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    media = supervisor.media_dir
    clips = [complete_clip(".mp4")] * 4
    fake_manim.plan({**video_render(media, "480p15", clips), **video_render(media, "1080p30", clips)}, hold=True)
    _send(supervisor, cmd="render_preview", id="preview-1")
    preview = _current(supervisor)
    stdout.wait_for("preview-1", "started")
    fake_manim.wait_until_held()
    path = {"path": str(_journey(tmp_path / "other"))} if command == "open_project" else {}
    _send(supervisor, cmd=command, id="next", **path)
    following = supervisor._current
    _join(preview)
    fake_manim.release()
    if following is not None:
        _join(following)
    cancelled = {"event": "error", "jobId": "preview-1", "code": "cancelled", "message": "cancelled"}
    assert stdout.of("preview-1") == [{"event": "started", "jobId": "preview-1"}, cancelled]
    replies = stdout.of("next")
    assert stdout.lines.index(cancelled) < stdout.lines.index(replies[0])
    assert (replies[0]["event"], replies[-1]["event"]) == first_and_last


@pytest.mark.usefixtures("quick_kill")
def test_held_previews_are_cancelled_while_the_export_runs_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    fake_manim.plan(video_render(supervisor.media_dir, "1080p30", [complete_clip(".mp4")] * 4), hold=True)
    _send(supervisor, cmd="export", id="export-1")
    export = _current(supervisor)
    stdout.wait_for("export-1", "started")
    fake_manim.wait_until_held()
    _send(supervisor, cmd="render_preview", id="preview-2")
    _send(supervisor, cmd="render_preview", id="preview-3")
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-3")
    _send(supervisor, cmd="export", id="export-2")
    cancelled = {"event": "error", "code": "cancelled", "message": "cancelled"}
    assert stdout.of("preview-2") == [{"jobId": "preview-2", **cancelled}]
    assert stdout.of("preview-3") == [{"jobId": "preview-3", **cancelled}]
    assert stdout.of("cancel-1") == []
    assert stdout.of("export-2") == [
        {"event": "rejected", "jobId": "export-2", "code": "export_running", "message": "An export is already running."}
    ]
    assert not export.ended
    fake_manim.release()
    _join(export)
    assert [line["event"] for line in stdout.of("export-1")] == ["started", "done"]
    assert supervisor._current is None
    assert supervisor.log_path.read_text(encoding="utf-8").count("argv: ") == 1


# Robust kills (F025, F097, F098)


def test_a_cancel_whose_kill_helper_fails_still_ends_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "0")

    def helper_gone(proc: object) -> None:
        raise OSError("process table helper exited")

    monkeypatch.setattr(supervisor_module, "terminate_process_group", helper_gone)
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    fake_manim.plan(video_render(supervisor.media_dir, "480p15", [complete_clip(".mp4")] * 4), hold=True)
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    stdout.wait_for("preview-1", "started")
    fake_manim.wait_until_held()
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
    _join(job)
    _send(supervisor, cmd="status", id="after")
    assert [line["event"] for line in stdout.of("preview-1")] == ["started", "error"]
    assert stdout.of("preview-1")[-1]["code"] == "cancelled"
    assert stdout.of("cancel-1") == []
    assert stdout.last()["event"] == "ready"
    assert "process tree terminate failed: process table helper exited" in supervisor.log_path.read_text(
        encoding="utf-8"
    )


def test_a_cancelled_job_closes_its_job_object_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    terminated: list[int] = []
    closed: list[int] = []
    monkeypatch.setattr(supervisor_module, "jobs_enabled", lambda: True)
    monkeypatch.setattr(supervisor_module, "assign_kill_on_close", lambda pid: 4242)
    monkeypatch.setattr(supervisor_module, "terminate_job", terminated.append)
    monkeypatch.setattr(supervisor_module, "close_job", closed.append)
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    fake_manim.plan(video_render(supervisor.media_dir, "480p15", [complete_clip(".mp4")] * 4), hold=True)
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    stdout.wait_for("preview-1", "started")
    fake_manim.wait_until_held()
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
    # The stand-in job kills nothing, so the fake Manim ends by itself.
    fake_manim.release()
    _join(job)
    assert (terminated, closed) == ([4242], [4242])
    assert [line["event"] for line in stdout.of("preview-1")] == ["started", "error"]
    assert stdout.of("preview-1")[-1]["code"] == "cancelled"


def test_a_failed_job_object_terminate_falls_back_to_the_tree_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    killed: list[int] = []

    def refused(handle: int) -> None:
        raise OSError(5, "Access is denied")

    def tree_kill(proc: subprocess.Popen[str]) -> None:
        killed.append(proc.pid)
        proc.kill()

    monkeypatch.setattr(supervisor_module, "terminate_job", refused)
    monkeypatch.setattr(supervisor_module, "terminate_process_group", tree_kill)
    proc = _sleeper()
    job = _Job("preview-1", "preview", threading.current_thread(), threading.Event(), proc=proc, win_job=42)
    supervisor._kill_process(job)
    assert proc.wait(timeout=10) is not None
    assert killed == [proc.pid]
    assert "job terminate failed: [Errno 5] Access is denied" in supervisor.log_path.read_text(encoding="utf-8")


@pytest.mark.usefixtures("quick_kill")
def test_a_failure_after_manim_started_kills_it_and_releases_its_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    fake_manim.plan(video_render(supervisor.media_dir, "480p15", [complete_clip(".mp4")] * 4), hold=True)

    def broken(media_dir: Path) -> None:
        raise RuntimeError("progress bug")

    monkeypatch.setattr(supervisor_module, "ManimProgress", broken)
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    _join(job)
    assert job.proc is not None
    assert job.proc.wait(timeout=10) is not None
    assert job.win_job is None
    started, failed = stdout.of("preview-1")
    assert started == {"event": "started", "jobId": "preview-1"}
    assert (failed["event"], failed["code"]) == ("error", "internal")
    assert "job preview-1 failed" in supervisor.log_path.read_text(encoding="utf-8")


def test_the_process_table_reports_a_failed_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    table = process_group._WindowsProcessTable()
    monkeypatch.setattr(table, "_ask", lambda command: ["ERROR Access is denied"])
    with pytest.raises(OSError, match="Access is denied"):
        table.pids(fresh=True)
    monkeypatch.setattr(table, "_ask", lambda command: [])
    with pytest.raises(OSError, match="no processes"):
        table.pids(fresh=True)


@pytest.mark.skipif(sys.platform != "win32", reason="the process table is the Windows tree kill")
def test_a_failed_process_table_still_kills_the_root(monkeypatch: pytest.MonkeyPatch) -> None:
    def helper_gone(*, fresh: bool) -> list[tuple[int, int, str, str]]:
        raise OSError("process table helper exited")

    monkeypatch.setattr(process_group._TABLE, "pids", helper_gone)
    proc = _sleeper()
    with pytest.raises(OSError, match="helper exited"):
        process_group.terminate_process_group(proc)
    assert proc.poll() is not None


# Hand-written scene mode (C7.7, D42)


def test_hand_written_mode_renders_the_scene_in_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(handwritten_project(tmp_path / "handwritten", REFERENCE), monkeypatch, init=False)
    _send(supervisor, cmd="status", id="status")
    assert stdout.last()["startupError"] == {"code": "not_a_project", "message": NOT_A_PROJECT}
    for command in ("get_state", "undo", "redo", "code", "clear_cache"):
        _send(supervisor, cmd=command, id=command)
        assert stdout.last() == {"event": "rejected", "jobId": command, "code": "no_project", "message": NO_PROJECT}
    _send(supervisor, cmd="edit", id="edit", edit={"op": "set_project_name", "name": "Named"})
    assert stdout.last()["code"] == "no_project"

    media = supervisor.media_dir
    clips = [complete_clip(".mp4")] * 2
    fake_manim.plan({**video_render(media, "480p15", clips), **video_render(media, "1080p30", clips)})
    _run(supervisor, cmd="render_preview", id="preview-1")
    started, *beats, done = stdout.of("preview-1")
    assert started == {"event": "started", "jobId": "preview-1"}
    assert [(beat["event"], beat["beatIndex"]) for beat in beats] == [("beat_ready", 0), ("beat_ready", 1)]
    assert done == {
        "event": "done",
        "jobId": "preview-1",
        "path": str(media / "videos" / "scene" / "480p15" / "MainScene.mp4"),
    }

    _run(supervisor, cmd="export", id="export-1", force=True)
    assert stdout.of("export-1") == [
        {"event": "started", "jobId": "export-1"},
        {"event": "done", "jobId": "export-1", "path": str(media / "videos" / "scene" / "1080p30" / "MainScene.mp4")},
    ]
    assert not (supervisor.project / ".mathimator" / "exports").exists()

    supervisor.scene_path.unlink()
    _run(supervisor, cmd="render_preview", id="preview-2")
    assert stdout.of("preview-2") == [
        {
            "event": "rejected",
            "jobId": "preview-2",
            "code": "no_project",
            "message": f"No project is open, and {supervisor.scene_path} does not exist.",
        }
    ]


def test_a_refused_project_never_renders_a_scene_left_in_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    folder = handwritten_project(tmp_path / "refused", REFERENCE)
    (folder / "project.json").write_text("{", encoding="utf-8")
    supervisor = _supervisor(folder, monkeypatch, init=False)
    _run(supervisor, cmd="render_preview", id="preview-1")
    (refused,) = stdout.of("preview-1")
    assert (refused["event"], refused["code"]) == ("rejected", "no_project")
    assert refused["message"].startswith("No project is open. project.json is not valid JSON: ")
    assert not fake_manim.ran()


def _supervisor(project: Path, monkeypatch: pytest.MonkeyPatch, *, init: bool = True) -> Supervisor:
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    project.mkdir(parents=True, exist_ok=True)
    return Supervisor(project, sys.executable, project.parent / f"{project.name}.log", init)


def _journey(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "project.json").write_bytes(JOURNEY.read_bytes())
    return root


def _with_index(project: Path, clip: str) -> Path:
    """Store a preview index whose every beat names ``clip`` as its partial and last good movie."""
    beats = [
        {"id": f"beat_{number}", "index": number - 1, "partial": clip, "lastGood": clip, "fresh": True}
        for number in range(1, 5)
    ]
    cache = project / ".mathimator" / "cache"
    cache.mkdir(parents=True)
    (cache / "preview-index.json").write_text(json.dumps({"output": None, "beats": beats}), encoding="utf-8")
    return project


def _send(supervisor: Supervisor, **request: object) -> None:
    supervisor.handle_line(json.dumps(request))


def _run(supervisor: Supervisor, **request: object) -> None:
    """Send a request and wait for the job it starts, if it starts one."""
    _send(supervisor, **request)
    job = supervisor._current
    if job is not None:
        _join(job)


def _current(supervisor: Supervisor) -> _Job:
    job = supervisor._current
    assert job is not None
    return job


def _join(job: _Job) -> None:
    job.thread.join(timeout=60)
    assert not job.thread.is_alive()


def _undo_depth(supervisor: Supervisor) -> int:
    assert supervisor.session is not None
    return len(supervisor.session._undo)


def _sleeper() -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], text=True)
