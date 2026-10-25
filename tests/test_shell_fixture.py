"""The worker started as the shell starts it answers requests.json as expected and as replies.json records (PR1 C).

The worker starts on a debug shell's spawn line (lib.rs ``supervisor_command``
and runtime.rs ``python_command``): behind a Windows venv launcher the real
interpreter with ``__PYVENV_LAUNCHER__``, ``-P -m mathimator_worker --project
<folder> --python <venv python> --init``, no console window, the inherited
``PYTHON*``, ``TEXMF*`` and ``TEXINPUTS`` variables and ``MATHIMATOR_TEX_ERROR``
removed, the shell's ``PYTHON*`` settings, and a working folder of its own.
Only the fake Manim goes ahead of the worker on PYTHONPATH, and
``MATHIMATOR_SKIP_PROCESS_TABLE=1`` keeps the process table helper from
starting. Each request goes in the bytes the shell writes, and each line comes
back as the shell reads it.

The fake Manim renders the two beats of each job wherever the worker asks it
to (an auto plan).

replies.json keeps those lines for protocolGolden.test.ts, which reads them as
the UI does. Each folder is written as its placeholder, and the path below it
with ``/`` between names, so the file is the same on every OS. This test checks
that the worker still writes lines of the same keys and types.
``uv run pytest tests/test_shell_fixture.py --update-golden`` rewrites the file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from functools import partial
from pathlib import Path, PurePath
from typing import Any

import pytest

from mathimator_worker.jsonio import loads
from tests.support import (
    REPO,
    TERMINAL_EVENTS,
    WORKER,
    FakeManim,
    SupervisorClient,
    holds,
    shared_requests,
    venv_python,
)

GOLDEN = REPO / "tests" / "fixtures" / "protocol" / "replies.json"
# The commands that start a job, whose lines end with its one terminal line (C7.1).
JOBS = frozenset({"render_preview", "export"})


def test_the_worker_the_shell_starts_answers_the_shared_requests(
    tmp_path: Path, fake_manim: FakeManim, pytestconfig: pytest.Config
) -> None:
    """Send each line of requests.json in turn, each job to its end, and check the lines that carry its id."""
    project, other, work = (_folder(tmp_path / name) for name in ("project", "other", "work"))
    places = {"{project}": str(project), "{other}": str(other)}
    rows = shared_requests(places)
    fake_manim.auto(beats=2)
    replies = _replay(SupervisorClient(_spawn(project, work, fake_manim)), rows)
    bound: str | None = None
    for row, lines in zip(rows, replies, strict=True):
        assert len(lines) == len(row["expect"]), (row["line"], lines)
        for expected, line in zip(row["expect"], lines, strict=True):
            assert holds(line, expected), (row["line"], line)
        bound = next((line["projectPath"] for line in lines if line["event"] == "state"), bound)
        if row["line"]["cmd"] in JOBS:
            ends = [line["event"] in TERMINAL_EVENTS for line in lines]
            assert ends == [False] * (len(lines) - 1) + [True], lines
            assert bound is not None
            assert all(_inside(line["path"], bound) for line in lines if "path" in line), (bound, lines)

    back = {folder: placeholder for placeholder, folder in places.items()}
    recorded = [
        {"line": _golden(row["line"], back), "replies": _golden(lines, back)}
        for row, lines in zip(rows, replies, strict=True)
    ]
    if pytestconfig.getoption("update_golden"):
        text = json.dumps({"requests": recorded}, indent=2, ensure_ascii=False) + "\n"
        GOLDEN.write_text(text, encoding="utf-8", newline="\n")
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["requests"]
    stale = "replies.json is out of date: rewrite it with uv run pytest tests/test_shell_fixture.py --update-golden"
    assert [entry["line"] for entry in golden] == [entry["line"] for entry in recorded], stale
    for kept, entry in zip(golden, recorded, strict=True):
        assert _shape(entry["replies"]) == _shape(kept["replies"]), (stale, entry["line"])


def _replay(client: SupervisorClient, rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The lines that carry the id of each row's request, read as the shell reads them; there are no others.

    Each request goes once the last line the one before expects has come, and
    the worker writes that line after the others it expects (C7.1).
    """
    try:
        for row in rows:
            client.send(row["line"])
            if row["expect"]:
                last = {"jobId": row["line"].get("id"), "event": row["expect"][-1]["event"]}
                client.wait_for(partial(holds, expected=last), timeout=60)
    finally:
        client.close()
    written = [_as_the_shell_reads(raw) for raw in client.raw_lines]
    replies = [[line for line in written if line["jobId"] == row["line"].get("id")] for row in rows]
    assert sum(map(len, replies)) == len(written), written
    return replies


def _folder(path: Path) -> Path:
    path.mkdir()
    return path.resolve()


def _spawn(project: Path, work: Path, fake_manim: FakeManim) -> subprocess.Popen[bytes]:
    """The worker on the new temp project ``project``, started as a debug shell starts it."""
    python = venv_python()
    base = _venv_base(python)
    env = {name: value for name, value in os.environ.items() if not _cleaned(name)}
    env.pop("MATHIMATOR_TEX_ERROR", None)
    if base is not None:
        env["__PYVENV_LAUNCHER__"] = str(python)
    env.update(
        PYTHONPATH=os.pathsep.join([str(fake_manim.package.parent), str(WORKER)]),
        PYTHONUNBUFFERED="1",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        MATHIMATOR_SKIP_PROCESS_TABLE="1",
    )
    argv = [str(base or python), "-P", "-m", "mathimator_worker"]
    argv += ["--project", str(project), "--python", str(python), "--init"]
    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=work,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _cleaned(name: str) -> bool:
    """Whether a debug shell keeps the inherited variable ``name`` from the worker (runtime.rs ``is_cleaned``)."""
    name = name.upper()
    return name.startswith(("PYTHON", "TEXMF")) or name == "TEXINPUTS"


def _venv_base(python: Path) -> Path | None:
    """The real interpreter behind ``python`` when it is a Windows venv launcher (runtime.rs ``venv_base``)."""
    if sys.platform != "win32":
        return None
    configs = [folder / "pyvenv.cfg" for folder in (python.parent, python.parent.parent)]
    config = next((path for path in configs if path.is_file()), None)
    if config is None:
        return None
    for line in config.read_text(encoding="utf-8").splitlines():
        key, _, home = line.partition("=")
        if key.strip() == "home":
            base = Path(home.strip()) / python.name
            return base if base.is_file() else None
    return None


def _as_the_shell_reads(raw: bytes) -> dict[str, Any]:
    """A stdout line parsed as the shell parses it: a whole line, strict UTF-8 and JSON with finite numbers only."""
    assert raw.endswith(b"\n"), raw
    line = loads(raw.decode("utf-8"))
    assert isinstance(line, dict), raw
    return line


def _inside(path: str, folder: str) -> bool:
    """Whether ``path`` is the path string ``folder`` joined with a relative path inside it (C5).

    The shell grants the webview the folder by that string, so a path must
    spell it exactly, case and separators included, and must not leave it
    through ``..``.
    """
    relative = path.removeprefix(folder + os.sep)
    return relative != path and ".." not in PurePath(relative).parts


def _golden(value: Any, back: Mapping[str, str]) -> Any:
    """The JSON ``value`` as replies.json keeps it, the same on every OS.

    A path in a folder of ``back`` becomes its placeholder, followed by the
    names below the folder with ``/`` between them.
    """
    if isinstance(value, dict):
        return {key: _golden(item, back) for key, item in value.items()}
    if isinstance(value, list):
        return [_golden(item, back) for item in value]
    if isinstance(value, str):
        for folder, placeholder in back.items():
            if value.startswith(folder):
                return placeholder + value.removeprefix(folder).replace(os.sep, "/")
    return value


def _shape(value: object) -> object:
    """The keys and types of a JSON value, which the golden lines keep while the values may change."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape(item) for item in value]
    return type(value).__name__
