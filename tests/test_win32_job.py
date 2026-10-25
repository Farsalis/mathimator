"""Job Object cancel and quit.

Set ``MATHIMATOR_WIN32_TESTS=1`` to run these; the owner approved running them
on this PC and on the Windows CI runner (D14). They set
``MATHIMATOR_WIN32_JOBS=1`` so the supervisor assigns each Manim child to a
kill-on-close job.

The stand-ins are compiled from ``tests/fixtures/win32``. The cancel test runs
the supervisor through ``launcher.exe``, shaped like a venv's python.exe: it
starts the real interpreter at once. The supervisor must start Manim with that
real interpreter (D16), and Manim, a fake ``manim`` package, starts
``latex.exe`` and ``dvisvgm.exe`` at once, with no handshake, as a render of a
formula does. In the quit test the supervisor runs the real interpreter, and
the Manim stand-in, ``job_tree.cs``, waits for a go file the test writes after
the supervisor logs ``job assigned``, so both helpers are born inside the job.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

from mathimator_worker.process_group import process_alive
from tests.support import (
    WIN32_FIXTURES,
    FakeManim,
    SupervisorClient,
    compile_csharp,
    read_argv,
    start_supervisor,
    venv_python,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("MATHIMATOR_WIN32_TESTS") != "1",
    reason="Win32 job-object tests run only with MATHIMATOR_WIN32_TESTS=1",
)

# The main of the fake manim package: it starts the TeX stand-ins at once, writes their pids and sleeps.
_TEX_TREE = """\
import os
import subprocess
import time
from pathlib import Path

children = [subprocess.Popen([os.environ[name]]) for name in ("MATHIMATOR_TEST_LATEX", "MATHIMATOR_TEST_DVISVGM")]
Path(os.environ["MATHIMATOR_TEST_PIDS"]).write_text("".join(f"{child.pid}\\n" for child in children), encoding="utf-8")
time.sleep(30)
"""


def test_cancel_reaps_manim_latex_and_dvisvgm_within_one_second(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "1")
    root = tmp_path / "tree"
    root.mkdir()
    pid_file = _tex_stand_ins(root, monkeypatch)
    launcher = compile_csharp(WIN32_FIXTURES / "launcher.cs", root / "python.exe")
    monkeypatch.setenv("MATHIMATOR_TEST_REAL_PYTHON", str(_base_python()))
    fake = FakeManim(tmp_path / "fake-manim", monkeypatch)
    (fake.package / "__main__.py").write_text(_TEX_TREE, encoding="utf-8")
    log_path = tmp_path / "supervisor.log"
    # As in a debug build, the worker's own interpreter is a venv launcher, and --python names it too.
    client = start_supervisor(
        tmp_path / "project",
        log_path,
        python=launcher,
        init=True,
        supervisor_python=launcher,
        pythonpath=[fake.package.parent],
    )
    try:
        _ready(client, "status")
        client.send({"cmd": "render_preview", "id": "preview-1"})
        manim_pid = int(_matching_line(log_path, "job assigned pid=", 15).rsplit("pid=", 1)[1])
        assert os.path.samefile(read_argv(log_path)[0], _base_python())
        pids = [manim_pid, *_read_child_pids(pid_file, 10)]
        assert all(process_alive(pid) for pid in pids), pids
        client.send({"cmd": "cancel", "id": "cancel-1", "jobId": "preview-1"})
        _assert_gone_within_one_second(pids)
        ended = client.wait_for(lambda event: event.get("jobId") == "preview-1" and event.get("event") == "error", 10)
        assert (ended["code"], ended["message"]) == ("cancelled", "cancelled")
        _ready(client, "after-cancel")
        assert client.proc.poll() is None
        assert "job terminate failed" not in log_path.read_text(encoding="utf-8")
    finally:
        client.close()


def test_quit_reaps_manim_latex_and_dvisvgm_within_one_second(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _job_tree(tmp_path, monkeypatch) as (client, pids):
        # close() waits 5s for a graceful exit. Quit is the process dying now.
        client.proc.kill()
        _assert_gone_within_one_second(pids)


def _assert_gone_within_one_second(pids: list[int]) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not any(process_alive(pid) for pid in pids):
            return
        time.sleep(0.02)
    alive = [pid for pid in pids if process_alive(pid)]
    raise AssertionError(f"still alive after 1s: {alive}")


@contextmanager
def _job_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[SupervisorClient, list[int]]]:
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "1")
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    root = tmp_path / "tree"
    root.mkdir()
    pid_file = _tex_stand_ins(root, monkeypatch)
    manim = compile_csharp(WIN32_FIXTURES / "job_tree.cs", root / "manim.exe")
    go = root / "go"
    monkeypatch.setenv("MATHIMATOR_TEST_GO", str(go))
    log_path = tmp_path / "supervisor.log"
    client = start_supervisor(
        tmp_path / "project",
        log_path,
        python=manim,
        init=True,
        supervisor_python=_base_python(),
    )
    try:
        _ready(client, "status")
        client.send({"cmd": "render_preview", "id": "preview-1"})
        manim_pid = int(_matching_line(log_path, "job assigned pid=", 15).rsplit("pid=", 1)[1])
        go.write_text("go", encoding="utf-8")
        pids = [manim_pid, *_read_child_pids(pid_file, 10)]
        assert all(process_alive(pid) for pid in pids), pids
        yield client, pids
    finally:
        if client.proc.poll() is None:
            client.proc.kill()
        with suppress(subprocess.TimeoutExpired):
            client.proc.wait(timeout=5)


def _tex_stand_ins(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Compile ``latex.exe`` and ``dvisvgm.exe`` and name them to the Manim stand-in. Return its pid file."""
    for name in ("latex", "dvisvgm"):
        exe = compile_csharp(WIN32_FIXTURES / "sleeper.cs", root / f"{name}.exe")
        monkeypatch.setenv(f"MATHIMATOR_TEST_{name.upper()}", str(exe))
    pid_file = root / "pids.txt"
    monkeypatch.setenv("MATHIMATOR_TEST_PIDS", str(pid_file))
    return pid_file


def _ready(client: SupervisorClient, job_id: str) -> None:
    client.send({"cmd": "status", "id": job_id})
    client.wait_for(lambda event: event.get("event") == "ready" and event.get("jobId") == job_id, 15)


def _base_python() -> Path:
    """The real interpreter behind the venv launcher.

    ``Scripts/python.exe`` stays alive and starts a second ``python.exe``.
    The job handle is held by the process that runs the supervisor, so quit
    has to kill that process.
    """
    launcher = venv_python()
    cfg = launcher.parent.parent / "pyvenv.cfg"
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.startswith("home = "):
                candidate = Path(line.split("=", 1)[1].strip()) / "python.exe"
                if candidate.is_file():
                    return candidate
    return launcher


def _matching_line(log_path: Path, needle: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.is_file():
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if needle in line:
                    return line
        time.sleep(0.05)
    detail = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else "missing"
    raise TimeoutError(detail[-2000:])


def _read_child_pids(path: Path, timeout: float) -> tuple[int, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(lines) >= 2:
                return int(lines[0]), int(lines[1])
        time.sleep(0.05)
    raise TimeoutError(f"child pids were not written to {path}")
