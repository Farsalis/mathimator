"""Protocol 2: the request envelope, replies, every refusal, cancel, the TeX state and hand-written mode (C2 to C8).

Every test runs a Supervisor in this process through ``handle_line`` and reads
its replies from the captured stdout. Each line is parsed the strict way the
shell parses it, so a line with NaN or any other non-JSON fails the test. Jobs
run the ``fake_manim`` stand-in. Together with the job, lifecycle and document
tests, each refusal code of each command in C4 is sent here at least once.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from mathimator_worker import supervisor as supervisor_module
from mathimator_worker.codegen import CodegenError
from mathimator_worker.document import (
    EXPORT_FORMATS,
    EXPORT_FPS,
    EXPORT_SIZES,
    FORMATS,
    MAX_TOTAL_SECONDS,
    MIN_BEAT_SECONDS,
    NOT_UTF8,
)
from mathimator_worker.preview_index import PreviewIndex
from mathimator_worker.progress import ManimProgress
from mathimator_worker.protocol import PROTOCOL_VERSION, REQUEST_FIELDS
from mathimator_worker.session import Session
from mathimator_worker.supervisor import NOT_A_PROJECT, Supervisor, _Job
from tests.support import (
    REFERENCE,
    REPO,
    SHARED_REQUESTS,
    FakeManim,
    Stdout,
    complete_clip,
    handwritten_project,
    holds,
    link_folder,
    shared_requests,
    unreadable,
    video_render,
)

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"
SHARED_COMMANDS = REPO / "tests" / "fixtures" / "protocol" / "commands.json"
SHARED_EXPORT_CHOICES = REPO / "tests" / "fixtures" / "protocol" / "export-choices.json"
NO_PROJECT = f"No project is open. {NOT_A_PROJECT}"
TEX_ERROR = "Formulas and axis labels cannot render on this PC: ! I can't find file `check'. Reinstall Mathimator."
_JOURNEY_TEXT = JOURNEY.read_text(encoding="utf-8")
# For each document code, a project.json made from Journey A that breaks one of its rules, and the field it names.
DOCUMENT_DEFECTS: dict[str, tuple[bytes, str | None]] = {
    "not_utf8": (_JOURNEY_TEXT.encode("utf-16"), None),
    "invalid_json": (_JOURNEY_TEXT.replace('"seed": 1,', '"seed": 1').encode(), None),
    "invalid_text": (
        _JOURNEY_TEXT.replace("y = \\\\sin(x)", "y = \\u0000").encode(),
        "scene.objects[2].params.latex",
    ),
    "untrusted_latex": (
        _JOURNEY_TEXT.replace("y = \\\\sin(x)", "\\\\input{secret}").encode(),
        "scene.objects[2].params.latex",
    ),
    "unsupported_expression": (
        _JOURNEY_TEXT.replace('"sin(x)"', '"__import__(1)"').encode(),
        "scene.objects[1].params.expression",
    ),
    "limit_exceeded": (
        _JOURNEY_TEXT.replace("[-6.5, 6.5, 1]", "[-6.5, 6.5, 0.01]").encode(),
        "scene.objects[0].params.xRange",
    ),
    "invalid_document": (
        _JOURNEY_TEXT.replace('"id": "beat_1",', '"id": "beat_1",\n        "start": 0,').encode(),
        "scene.beats[0].start",
    ),
}
# What the write and render checks of C8.1 refuse, problem by problem.
_REFUSALS = {
    "moved": "project_unavailable",
    "deleted": "project_unavailable",
    "unreadable": "read_failed",
    "changed": "project_changed",
}
# Each command that writes or renders, a problem on disk, and its refusal, or None for a state reply.
DISK_CHECKS: list[tuple[str, str, str | None]] = [
    *(
        (command, problem, code)
        for command in ("edit", "undo", "redo", "render_preview", "export")
        for problem, code in _REFUSALS.items()
    ),
    # Overwrite skips the read and the fingerprint, reload the fingerprint, and clear_cache all but the folder.
    ("overwrite", "moved", "project_unavailable"),
    ("overwrite", "deleted", "project_unavailable"),
    ("overwrite", "changed", None),
    ("reload", "moved", "project_unavailable"),
    ("reload", "deleted", "project_unavailable"),
    ("reload", "unreadable", "read_failed"),
    ("reload", "changed", None),
    ("clear_cache", "moved", "project_unavailable"),
    ("clear_cache", "deleted", None),
    ("clear_cache", "changed", None),
    # A folder of the project's own that became a link stops only what deletes or renders there (O10).
    *((command, "linked", None) for command in ("edit", "undo", "redo", "overwrite", "reload")),
    *((command, "linked", "linked_folder") for command in ("render_preview", "export", "clear_cache")),
]


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
        pytest.param('{"cmd": "set_tex_error", "id": "r"}', "r", id="tex-error-missing"),
        pytest.param('{"cmd": "set_tex_error", "id": "r", "message": ""}', "r", id="tex-error-empty"),
        pytest.param('{"cmd": "set_tex_error", "id": "r", "message": 5}', "r", id="tex-error-not-a-string"),
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


@pytest.mark.parametrize("edit", [{"op": "no_such_op"}, {}, {"op": 5}, {"op": ["set_text"]}])
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
    saved = supervisor.layout.project_file.read_bytes()

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
    assert supervisor.layout.project_file.read_bytes() == saved
    log = supervisor.log_path.read_text(encoding="utf-8")
    assert "edit e failed\nTraceback (most recent call last):" in log
    assert "RuntimeError: handler bug" in log


def test_an_edit_its_template_would_undo_is_an_invalid_edit_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    saved = supervisor.layout.project_file.read_bytes()
    # The plot's color belongs to the axes_function template, which this edit now leaves alone (F072).
    monkeypatch.setattr("mathimator_worker.session._template_plot", lambda *args: None)
    _send(supervisor, cmd="edit", id="e", edit={"op": "set_object_color", "objectId": "plot_1", "color": "#FF0000"})
    assert stdout.read() == [
        {
            "event": "rejected",
            "jobId": "e",
            "code": "invalid_edit",
            "message": "scene.objects[1].params.color is set by its template, so this edit cannot change it.",
        }
    ]
    assert _undo_depth(supervisor) == 0
    assert supervisor.layout.project_file.read_bytes() == saved


def test_a_change_that_cannot_be_saved_is_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="rename", edit={"op": "set_project_name", "name": "Renamed"})
    saved = supervisor.layout.project_file.read_bytes()

    def locked(source: object, target: object) -> None:
        raise PermissionError(13, "The file is in use")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", locked)
        _send(supervisor, cmd="edit", id="edit", edit={"op": "set_project_name", "name": "Unsaved"})
        _send(supervisor, cmd="undo", id="undo")
    refusals = stdout.read()[1:]
    assert [(line["jobId"], line["code"]) for line in refusals] == [("edit", "save_failed"), ("undo", "save_failed")]
    assert refusals[0]["message"] == "Could not save project.json: The file is in use. The change was undone."
    assert supervisor.session is not None
    assert supervisor.session.project.name == "Renamed"
    assert (_undo_depth(supervisor), supervisor.session.can_redo()) == (1, False)
    assert supervisor.layout.project_file.read_bytes() == saved
    assert sorted(path.name for path in supervisor.project.iterdir()) == [".mathimator", "project.json"]
    _send(supervisor, cmd="undo", id="undo-again")
    assert stdout.last()["event"] == "state"
    assert json.loads(supervisor.layout.project_file.read_text(encoding="utf-8"))["name"] == "Sine introduction"
    undone = supervisor.layout.project_file.read_bytes()
    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", locked)
        _send(supervisor, cmd="redo", id="redo")
    assert (stdout.last()["jobId"], stdout.last()["code"]) == ("redo", "save_failed")
    assert (_undo_depth(supervisor), supervisor.session.can_redo()) == (0, True)
    assert supervisor.layout.project_file.read_bytes() == undone


def test_a_reply_json_cannot_hold_becomes_an_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    state_event = Session.state_event

    def with_nan(self: Session, job_id: str, project_path: str, index: PreviewIndex | None) -> dict[str, object]:
        event = state_event(self, job_id, project_path, index)
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
    monkeypatch.setattr(supervisor, "_log_override", tmp_path)
    supervisor.log("a folder cannot take this line")
    assert "a folder cannot take this line" in capsys.readouterr().err


def test_the_worker_requests_match_the_shared_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = json.loads(SHARED_COMMANDS.read_text(encoding="utf-8"))
    requests = shared["workerRequests"]
    assert {entry["worker"] for entry in shared["tauriCommands"]} - {None} <= set(requests)
    # The list writes each field's kind without its article, and "?" after an optional one.
    assert requests == {
        command: {name: kind.partition(" ")[2] + ("" if required else "?") for name, (kind, required) in fields.items()}
        for command, fields in REQUEST_FIELDS.items()
    }
    assert set(_supervisor(tmp_path / "project", monkeypatch)._commands) == set(requests)


def test_the_shared_requests_send_every_command_with_only_its_fields() -> None:
    lines = [row["line"] for row in json.loads(SHARED_REQUESTS.read_text(encoding="utf-8"))["requests"]]
    assert {line["cmd"] for line in lines} == set(REQUEST_FIELDS)
    for line in lines:
        assert set(line) - {"id", "cmd"} <= set(REQUEST_FIELDS[line["cmd"]]), line


def test_the_shared_requests_get_the_replies_they_expect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    """Send each line of requests.json in turn, each job to its end, and match the lines that carry its id."""
    supervisor = _supervisor(tmp_path / "project", monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    clips = [complete_clip(".mp4")] * 2
    fake_manim.plan(
        {**video_render(supervisor.layout, "480p15", clips), **video_render(supervisor.layout, "1080p30", clips)}
    )
    for row in shared_requests({"{project}": str(supervisor.project), "{other}": str(other.resolve())}):
        _run(supervisor, **row["line"])
        replies = stdout.of(row["line"].get("id"))
        assert len(replies) == len(row["expect"]), (row["line"], replies)
        for expected, reply in zip(row["expect"], replies, strict=True):
            assert holds(reply, expected), (row["line"], reply)


def test_the_export_choices_and_duration_limits_match_the_shared_list() -> None:
    shared = json.loads(SHARED_EXPORT_CHOICES.read_text(encoding="utf-8"))
    assert set(shared["formats"]) == EXPORT_FORMATS
    assert {(width, height) for width, height in shared["sizes"]} == EXPORT_SIZES
    assert set(shared["fps"]) == EXPORT_FPS
    assert shared["minBeatSeconds"] == MIN_BEAT_SECONDS
    assert shared["maxTotalSeconds"] == MAX_TOTAL_SECONDS
    opaque = {name: spec.opaque_reason for name, spec in FORMATS.items() if spec.opaque_reason is not None}
    assert shared["opaqueReasons"] == opaque


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

    second = _journey(tmp_path / "second")
    with monkeypatch.context() as patched:
        patched.setattr(Supervisor, "_rebuild_index_locked", broken)
        _send(supervisor, cmd="open_project", id="open", path=str(second))
    _send(supervisor, cmd="get_state", id="state")
    _send(supervisor, cmd="undo", id="undo")
    renamed, failed, state, undone = stdout.read()
    assert (failed["event"], failed["jobId"], failed["code"]) == ("error", "open", "internal")
    assert {**state, "jobId": "rename", "changed": True} == renamed
    assert supervisor.layout.project_file == first.resolve() / "project.json"
    assert (undone["event"], undone["document"]["name"]) == ("state", "Sine introduction")
    assert json.loads(supervisor.layout.project_file.read_text(encoding="utf-8"))["name"] == "Sine introduction"
    # The failed attempt released the second folder's lock and removed what it created there.
    assert sorted(path.name for path in second.iterdir()) == ["project.json"]


# Every refusal of each command (C4, C5.2, C8.1)


@pytest.mark.parametrize(
    ("edit", "code", "field", "message"),
    [
        pytest.param(
            {"op": "set_beat_duration", "beatId": "beat_9", "duration": 1},
            "invalid_edit",
            "beatId",
            "unknown beat beat_9",
            id="invalid_edit",
        ),
        pytest.param(
            {"op": "set_latex", "objectId": "title_1", "latex": "a\x00b"},
            "invalid_text",
            "latex",
            "latex contains a character Mathimator cannot store.",
            id="invalid_text",
        ),
        pytest.param(
            {"op": "set_expression", "objectId": "plot_1", "expression": "y"},
            "unsupported_expression",
            "expression",
            "This expression is not supported.",
            id="unsupported_expression",
        ),
        pytest.param(
            {"op": "set_beat_duration", "beatId": "beat_1", "duration": 0.05},
            "limit_exceeded",
            "duration",
            "duration must be at least 0.1 seconds",
            id="limit_exceeded",
        ),
        pytest.param(
            {"op": "set_latex", "objectId": "title_1", "latex": "\\input{secret}"},
            "untrusted_latex",
            "latex",
            "latex uses \\input, which formulas cannot use.",
            id="untrusted_latex",
        ),
    ],
)
def test_an_edit_refusal_names_its_code_and_field(
    edit: dict[str, object],
    code: str,
    field: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: Stdout,
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="edit", edit=edit)
    assert stdout.read() == [{"event": "rejected", "jobId": "edit", "code": code, "message": message, "field": field}]
    assert supervisor.layout.project_file.read_bytes() == JOURNEY.read_bytes()
    assert _undo_depth(supervisor) == 0


@pytest.mark.parametrize(("command", "code"), [("undo", "nothing_to_undo"), ("redo", "nothing_to_redo")])
def test_an_empty_history_is_refused(
    command: str, code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _send(supervisor, cmd=command, id=command)
    message = f"Nothing to {command}."
    assert stdout.read() == [{"event": "rejected", "jobId": command, "code": code, "message": message}]


@pytest.mark.parametrize("code", sorted(DOCUMENT_DEFECTS))
@pytest.mark.parametrize("command", ["open_project", "reload"])
def test_a_document_refusal_names_its_code_and_field(
    command: str, code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "bound"), monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="rename", edit=_rename("Renamed"))
    data, field = DOCUMENT_DEFECTS[code]
    if command == "open_project":
        refused_folder = tmp_path / "refused"
        refused_folder.mkdir()
        (refused_folder / "project.json").write_bytes(data)
        _send(supervisor, cmd="open_project", id="refused", path=str(refused_folder))
    else:
        supervisor.layout.project_file.write_bytes(data)
        _send(supervisor, cmd="reload", id="refused")
    _send(supervisor, cmd="get_state", id="state")
    _renamed, refused, state = stdout.read()
    assert (refused["event"], refused["code"], refused.get("field")) == ("rejected", code, field)
    assert refused["message"]
    # The binding, the document and its undo history are as they were (C8.3).
    assert state["projectPath"] == str(supervisor.project)
    assert (state["document"]["name"], state["derived"]["canUndo"]) == ("Renamed", True)


@pytest.mark.parametrize(("command", "problem", "code"), DISK_CHECKS)
def test_the_disk_checks_of_each_command_that_writes_or_renders(
    command: str,
    problem: str,
    code: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: Stdout,
    fake_manim: FakeManim,
) -> None:
    """C8.1: each command meets each problem with its refusal, or with a state reply when it skips that check."""
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="first", edit=_rename("First"))
    _send(supervisor, cmd="edit", id="second", edit=_rename("Second"))
    # One step to undo and one to redo.
    _send(supervisor, cmd="undo", id="undo")
    depth = _undo_depth(supervisor)
    fields = {"edit": _rename("Third")} if command == "edit" else {}
    with _disk_problem(supervisor, problem):
        before = _project_bytes(supervisor)
        _run(supervisor, cmd=command, id="request", **fields)
        after = _project_bytes(supervisor)
    (reply,) = stdout.of("request")
    if code is None:
        assert reply["event"] == "state"
        return
    assert (reply["event"], reply["code"]) == ("rejected", code)
    assert (after, _undo_depth(supervisor)) == (before, depth)
    # A moved folder is never made again (D57).
    assert supervisor.project.exists() is (problem != "moved")
    assert not fake_manim.ran()


def test_code_refuses_a_scene_that_cannot_be_generated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)

    def refused(project: object) -> str:
        raise CodegenError("eq_1 left the screen when it was transformed into eq_2")

    monkeypatch.setattr(supervisor_module, "generate", refused)
    _send(supervisor, cmd="code", id="code")
    assert stdout.read() == [
        {
            "event": "rejected",
            "jobId": "code",
            "code": "invalid_document",
            "message": "eq_1 left the screen when it was transformed into eq_2",
        }
    ]


def test_an_overwrite_that_cannot_be_saved_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="rename", edit=_rename("Renamed"))
    outside = supervisor.layout.project_file.read_bytes().replace(b"Renamed", b"Elsewhere")
    supervisor.layout.project_file.write_bytes(outside)

    def full(source: object, target: object) -> None:
        raise OSError(28, "There is not enough space on the disk")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", full)
        _send(supervisor, cmd="overwrite", id="overwrite")
    _send(supervisor, cmd="edit", id="again", edit=_rename("Again"))
    _renamed, refused, again = stdout.read()
    assert refused == {
        "event": "rejected",
        "jobId": "overwrite",
        "code": "save_failed",
        "message": "Could not save project.json: There is not enough space on the disk. The change was undone.",
    }
    assert supervisor.layout.project_file.read_bytes() == outside
    # The fingerprint is the one before the failed overwrite, so the file is still the outside one (D59).
    assert again["code"] == "project_changed"


@pytest.mark.parametrize("command", ["render_preview", "export"])
def test_a_job_without_a_project_is_refused(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    project = tmp_path / "utf16"
    project.mkdir()
    (project / "project.json").write_bytes(JOURNEY.read_text(encoding="utf-8").encode("utf-16"))
    supervisor = _supervisor(project, monkeypatch, init=False)
    _run(supervisor, cmd=command, id="job")
    assert stdout.read() == [
        {"event": "rejected", "jobId": "job", "code": "no_project", "message": f"No project is open. {NOT_UTF8}"}
    ]
    assert not fake_manim.ran()


@pytest.mark.parametrize("command", ["render_preview", "export"])
def test_a_job_that_fails_unexpectedly_ends_with_an_internal_error(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)

    def broken(project: object) -> bool:
        raise RuntimeError("start bug")

    monkeypatch.setattr(supervisor_module, "needs_latex", broken)
    _run(supervisor, cmd=command, id="job")
    _send(supervisor, cmd="status", id="after")
    failed, ready = stdout.read()
    assert failed == {
        "event": "error",
        "jobId": "job",
        "code": "internal",
        "message": "Internal error: RuntimeError: start bug. Details are in worker.log.",
    }
    assert ready["event"] == "ready"
    assert not fake_manim.ran()
    assert "job job failed\nTraceback (most recent call last):" in supervisor.log_path.read_text(encoding="utf-8")


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
    fake_manim.plan({}, later=[video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)])
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
    clips = [complete_clip(".mp4")] * 4
    fake_manim.plan(
        {},
        later=[
            {**video_render(supervisor.layout, "480p15", clips), **video_render(supervisor.layout, "1080p30", clips)}
        ],
    )
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
    fake_manim.plan(video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4), hold=True)
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


# The TeX state the shell sets (C9.7, C14 Q4)


def test_a_tex_error_refuses_later_formula_jobs_and_stops_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _send(supervisor, cmd="edit", id="rpc-1", edit=_rename("Waves"))
    fake_manim.plan(video_render(supervisor.layout, "1080p30", [complete_clip(".mp4")] * 4), hold=True)
    _send(supervisor, cmd="export", id="export-2")
    export = _current(supervisor)
    stdout.wait_for("export-2", "started")
    fake_manim.wait_until_held()
    _send(supervisor, cmd="set_tex_error", id="tex-3", message=TEX_ERROR)
    fake_manim.release()
    _join(export)
    _run(supervisor, cmd="render_preview", id="preview-4")
    assert stdout.of("tex-3") == []
    assert [line["event"] for line in stdout.of("export-2")] == ["started", "done"]
    assert stdout.of("preview-4") == [
        {"event": "rejected", "jobId": "preview-4", "code": "tex_unavailable", "message": TEX_ERROR}
    ]
    assert _undo_depth(supervisor) == 1


def test_a_null_tex_error_lets_formula_jobs_render_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    monkeypatch.setenv("MATHIMATOR_TEX_ERROR", TEX_ERROR)
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    _run(supervisor, cmd="render_preview", id="preview-1")
    _send(supervisor, cmd="set_tex_error", id="tex-2", message=None)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _run(supervisor, cmd="render_preview", id="preview-3")
    assert stdout.of("preview-1")[-1]["code"] == "tex_unavailable"
    assert stdout.of("tex-2") == []
    assert stdout.of("preview-3")[-1]["event"] == "done"


# Robust kills (F025, F097, F098)


def test_a_cancel_whose_kill_helper_fails_still_ends_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "0")

    def helper_gone(proc: object) -> None:
        raise OSError("process table helper exited")

    monkeypatch.setattr(supervisor_module, "terminate_process_group", helper_gone)
    supervisor = _supervisor(_journey(tmp_path / "journey"), monkeypatch, init=False)
    fake_manim.plan({}, later=[video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)])
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
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4), hold=True)

    def broken(progress: ManimProgress) -> list[tuple[int, Path]]:
        raise RuntimeError("progress bug")

    monkeypatch.setattr(ManimProgress, "poll", broken)
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


# Hand-written scene mode (C7.7, D42)


def test_hand_written_mode_renders_the_scene_in_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    supervisor = _supervisor(handwritten_project(tmp_path / "handwritten", REFERENCE), monkeypatch, init=False)
    _send(supervisor, cmd="status", id="status")
    assert stdout.last()["startupError"] == {"code": "not_a_project", "message": NOT_A_PROJECT}
    for command in ("get_state", "undo", "redo", "code", "clear_cache", "reload", "overwrite"):
        _send(supervisor, cmd=command, id=command)
        assert stdout.last() == {"event": "rejected", "jobId": command, "code": "no_project", "message": NO_PROJECT}
    _send(supervisor, cmd="edit", id="edit", edit={"op": "set_project_name", "name": "Named"})
    assert stdout.last()["code"] == "no_project"
    _send(supervisor, cmd="save_as", id="save", path=str(tmp_path))
    assert stdout.last()["code"] == "no_project"
    # Hand-written mode takes no lock.
    assert not (supervisor.project / ".mathimator" / "lock").exists()

    media = supervisor.layout.media_dir
    clips = [complete_clip(".mp4")] * 2
    fake_manim.plan(
        {**video_render(supervisor.layout, "480p15", clips), **video_render(supervisor.layout, "1080p30", clips)}
    )
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

    supervisor.layout.scene_path.unlink()
    _run(supervisor, cmd="render_preview", id="preview-2")
    assert stdout.of("preview-2") == [
        {
            "event": "rejected",
            "jobId": "preview-2",
            "code": "no_project",
            "message": f"No project is open, and {supervisor.layout.scene_path} does not exist.",
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


@contextlib.contextmanager
def _disk_problem(supervisor: Supervisor, problem: str) -> Iterator[None]:
    """Make ``problem`` happen to the bound project for as long as the block runs."""
    project_file = supervisor.layout.project_file
    match problem:
        case "unreadable":
            with unreadable(project_file):
                yield
            return
        case "moved":
            # Windows refuses to rename a folder while a file in it is open, so the lock lets go first.
            assert supervisor._binding is not None
            supervisor._binding.lock.release()
            supervisor.project.rename(supervisor.project.with_name(f"{supervisor.project.name} moved"))
        case "deleted":
            project_file.unlink()
        case "changed":
            project_file.write_bytes(project_file.read_bytes() + b" ")
        case "linked":
            elsewhere = supervisor.project.with_name(f"{supervisor.project.name} elsewhere")
            elsewhere.mkdir()
            link_folder(supervisor.layout.exports_dir, elsewhere)
    yield


def _project_bytes(supervisor: Supervisor) -> bytes | None:
    """project.json as it is on disk, or None when it cannot be read."""
    try:
        return supervisor.layout.project_file.read_bytes()
    except OSError:
        return None


def _rename(name: str) -> dict[str, object]:
    return {"op": "set_project_name", "name": name}


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
