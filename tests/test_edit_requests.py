"""Every edit the UI builds, sent as the shell sends it: the examples of edit-ops.json through ``Supervisor.handle_line``.

``desktop/src/edits.test.ts`` proves that the UI's edit builders produce
exactly the examples of ``tests/fixtures/protocol/edit-ops.json``, and the
shell forwards an edit unchanged inside an ``edit`` request (spec section 6).
Here a supervisor in this process is bound to each example's start document,
whose setup edits are ``edit`` requests too, and is sent the example. The
reply is a ``state`` with ``changed`` true and a focus that names what the
document holds. Its document is what project.json now holds, it compiles to
scene code, and its derived values have one entry per beat, with ``onScreen``
one more for after the last. ``get_state`` reads the same state back, undo
brings back the start document and redo the edit, and ``reload`` reads the
edited document back from disk. ``test_edit_ops.py`` applies the same
examples to a session directly and checks what each one leaves.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mathimator_worker.supervisor import Supervisor
from tests.support import REPO, Stdout

FIXTURE: dict[str, Any] = json.loads(
    (REPO / "tests" / "fixtures" / "protocol" / "edit-ops.json").read_text(encoding="utf-8")
)
EXAMPLES: list[tuple[str, dict[str, Any]]] = [
    (example["start"], example["edit"]) for op in FIXTURE["ops"].values() for example in op["examples"]
]
EXAMPLE_IDS = [f"{start}-{edit['op']}" for start, edit in EXAMPLES]


class _Worker:
    """A supervisor in this process, sent one request at a time, and the one line it answers each with."""

    def __init__(self, supervisor: Supervisor, stdout: Stdout) -> None:
        self.supervisor = supervisor
        self._stdout = stdout
        self._sent = 0

    def send(self, cmd: str, **fields: object) -> dict[str, Any]:
        self._sent += 1
        job_id = f"rpc-{self._sent}"
        self.supervisor.handle_line(json.dumps({"cmd": cmd, "id": job_id, **fields}))
        (reply,) = self._stdout.of(job_id)
        return reply

    def edited(self, edit: dict[str, Any]) -> dict[str, Any]:
        """The state reply of an edit, which must change the document."""
        reply = self.send("edit", edit=edit)
        assert (reply["event"], reply.get("changed")) == ("state", True), reply
        return reply


@pytest.mark.parametrize(("start", "edit"), EXAMPLES, ids=EXAMPLE_IDS)
def test_each_edit_the_ui_builds_is_applied_and_reads_back(
    start: str, edit: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    with _bound(tmp_path / "project", start, monkeypatch, stdout) as worker:
        before = worker.send("get_state")
        reply = worker.edited(edit)
        document = reply["document"]
        assert document != before["document"]
        project_file = worker.supervisor.layout.project_file
        assert json.loads(project_file.read_text(encoding="utf-8")) == document
        _check_state(reply)
        _check_focus(reply)
        assert {key: value for key, value in worker.send("get_state").items() if key != "jobId"} == {
            key: value for key, value in reply.items() if key not in ("jobId", "changed", "focus")
        }
        undone = worker.send("undo")
        assert undone["document"] == before["document"]
        assert (undone["derived"]["canUndo"], undone["derived"]["canRedo"]) == (before["derived"]["canUndo"], True)
        redone = worker.send("redo")
        assert redone["document"] == document
        assert (redone["derived"]["canUndo"], redone["derived"]["canRedo"]) == (True, False)
        reloaded = worker.send("reload")
        assert reloaded["event"] == "state", reloaded
        assert reloaded["document"] == document


@pytest.mark.parametrize("start", list(FIXTURE["starts"]))
def test_each_start_is_built_by_edits_the_worker_takes(
    start: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout
) -> None:
    with _bound(tmp_path / "project", start, monkeypatch, stdout) as worker:
        state = worker.send("get_state")
        assert state["derived"]["canUndo"] == bool(FIXTURE["starts"][start]["edits"])
        _check_state(state)


@contextlib.contextmanager
def _bound(folder: Path, start: str, monkeypatch: pytest.MonkeyPatch, stdout: Stdout) -> Iterator[_Worker]:
    """A supervisor bound to ``folder``, which holds the start document the fixture names, built by its setup edits."""
    setup = FIXTURE["starts"][start]
    fixture = setup["fixture"]
    folder.mkdir()
    if fixture is not None:
        shutil.copyfile(REPO / "tests" / "fixtures" / fixture / "project.json", folder / "project.json")
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    supervisor = Supervisor(folder, sys.executable, folder.parent / f"{folder.name}.log", fixture is None)
    try:
        worker = _Worker(supervisor, stdout)
        for edit in setup["edits"]:
            worker.edited(edit)
        yield worker
    finally:
        supervisor.close()


def _check_state(reply: dict[str, Any]) -> None:
    """A state whose document compiles and whose derived values cover every beat."""
    assert reply["event"] == "state", reply
    assert isinstance(reply["source"], str)
    assert "sourceError" not in reply
    beats = reply["document"]["scene"]["beats"]
    derived = reply["derived"]
    assert len(derived["starts"]) == len(derived["dirty"]) == len(beats)
    assert len(derived["onScreen"]) == len(beats) + 1


def _check_focus(reply: dict[str, Any]) -> None:
    """The focus of an edit reply names a beat and an object the document holds, or nothing."""
    scene = reply["document"]["scene"]
    focus = reply.get("focus", {"beatId": None, "objectId": None})
    assert set(focus) == {"beatId", "objectId"}
    assert focus["beatId"] in {None, *(beat["id"] for beat in scene["beats"])}
    assert focus["objectId"] in {None, *(item["id"] for item in scene["objects"])}
