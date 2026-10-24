"""Job Object cancel and quit.

Set ``MATHIMATOR_WIN32_TESTS=1`` to run these. They set
``MATHIMATOR_WIN32_JOBS=1`` so the supervisor assigns each Manim child to a
kill-on-close job.

The stand-ins are compiled from ``tests/fixtures/win32``. The one named
``manim.exe`` waits until a go file exists, then starts ``latex.exe`` and
``dvisvgm.exe`` with ``UseShellExecute`` false. The supervisor logs
``job assigned`` before the test creates that file, so both helpers are born
inside the job.
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
from tests.support import WIN32_FIXTURES, SupervisorClient, compile_csharp, start_supervisor, venv_python

pytestmark = pytest.mark.skipif(
    os.environ.get("MATHIMATOR_WIN32_TESTS") != "1",
    reason="Win32 job-object tests run only with MATHIMATOR_WIN32_TESTS=1",
)


def test_cancel_reaps_manim_latex_and_dvisvgm_within_one_second(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _running_tree(tmp_path, monkeypatch) as (client, pids):
        client.send({"cmd": "cancel", "id": "cancel-1", "jobId": "preview-1"})
        _assert_gone_within_one_second(pids)


def test_quit_reaps_manim_latex_and_dvisvgm_within_one_second(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _running_tree(tmp_path, monkeypatch) as (client, pids):
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
def _running_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[SupervisorClient, list[int]]]:
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "1")
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    root = tmp_path / "tree"
    root.mkdir()
    manim, latex, dvisvgm = _compile_tree(root)
    go = root / "go"
    pid_file = root / "pids.txt"
    monkeypatch.setenv("MATHIMATOR_TEST_GO", str(go))
    monkeypatch.setenv("MATHIMATOR_TEST_PIDS", str(pid_file))
    monkeypatch.setenv("MATHIMATOR_TEST_LATEX", str(latex))
    monkeypatch.setenv("MATHIMATOR_TEST_DVISVGM", str(dvisvgm))
    project = tmp_path / "project"
    log_path = tmp_path / "supervisor.log"
    client = start_supervisor(
        project,
        log_path,
        python=manim,
        init=True,
        supervisor_python=_base_python(),
    )
    try:
        client.send({"cmd": "status", "id": "status"})
        client.wait_for(
            lambda event: event.get("event") == "ready" and event.get("jobId") == "status",
            15,
        )
        client.send({"cmd": "render_preview", "id": "preview-1"})
        line = _matching_line(log_path, "job assigned pid=", 15)
        manim_pid = int(line.rsplit("pid=", 1)[1].strip())
        go.write_text("go", encoding="utf-8")
        latex_pid, dvisvgm_pid = _read_child_pids(pid_file, 10)
        pids = [manim_pid, latex_pid, dvisvgm_pid]
        assert all(process_alive(pid) for pid in pids), pids
        yield client, pids
    finally:
        if client.proc.poll() is None:
            client.proc.kill()
        with suppress(subprocess.TimeoutExpired):
            client.proc.wait(timeout=5)


def _base_python() -> Path:
    """Use the real interpreter, not the venv launcher.

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


def _compile_tree(root: Path) -> tuple[Path, Path, Path]:
    manim = compile_csharp(WIN32_FIXTURES / "job_tree.cs", root / "manim.exe")
    latex = compile_csharp(WIN32_FIXTURES / "sleeper.cs", root / "latex.exe")
    dvisvgm = compile_csharp(WIN32_FIXTURES / "sleeper.cs", root / "dvisvgm.exe")
    return manim, latex, dvisvgm
