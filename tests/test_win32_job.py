"""Job Object cancel and quit. Not executed until Craig approves it.

Set ``MATHIMATOR_WIN32_TESTS=1`` to run these. They set
``MATHIMATOR_WIN32_JOBS=1`` so the supervisor assigns each Manim child to a
kill-on-close job. Do not set either variable on this machine until then.

The stand-in named ``manim.exe`` waits until a go file exists, then starts
``latex.exe`` and ``dvisvgm.exe`` with ``UseShellExecute`` false. The
supervisor logs ``job assigned`` before the test creates that file, so both
helpers are born inside the job.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from mathimator_worker.process_group import process_alive
from tests.support import SupervisorClient, start_supervisor, venv_python

pytestmark = pytest.mark.skipif(
    os.environ.get("MATHIMATOR_WIN32_TESTS") != "1",
    reason=(
        "Win32 job-object tests are written but not executed until Craig "
        "approves running them on this machine"
    ),
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
        try:
            client.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


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
    manim = _compile(root, "manim.exe", _TREE_PARENT)
    latex = _compile(root, "latex.exe", _SLEEPER)
    dvisvgm = _compile(root, "dvisvgm.exe", _SLEEPER)
    return manim, latex, dvisvgm


def _compile(root: Path, name: str, source: str) -> Path:
    exe = root / name
    source_path = root / f"{exe.stem}.cs"
    source_path.write_text(source, encoding="utf-8")
    windir = os.environ.get("WINDIR", r"C:\Windows")
    compiler = Path(windir) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    completed = subprocess.run(
        [str(compiler), "/nologo", "/t:exe", f"/out:{exe}", str(source_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not exe.is_file():
        detail = (completed.stdout + completed.stderr).strip()
        raise RuntimeError(f"csc failed for {exe}: {detail}")
    return exe


_SLEEPER = "class Sleeper { static void Main() { System.Threading.Thread.Sleep(30000); } }\n"

_TREE_PARENT = r"""using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
class JobTree {
    static int Main() {
        string go = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_GO");
        string pids = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_PIDS");
        string latex = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_LATEX");
        string dvi = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_DVISVGM");
        if (go == null || pids == null || latex == null || dvi == null) return 2;
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while (!File.Exists(go)) {
            if (DateTime.UtcNow > deadline) return 3;
            Thread.Sleep(20);
        }
        var latexProc = StartHidden(latex);
        var dviProc = StartHidden(dvi);
        File.WriteAllText(pids, latexProc.Id + "\n" + dviProc.Id + "\n");
        Thread.Sleep(30000);
        return 0;
    }
    static Process StartHidden(string path) {
        var info = new ProcessStartInfo();
        info.FileName = path;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        return Process.Start(info);
    }
}
"""
