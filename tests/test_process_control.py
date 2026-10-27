"""Process control: the fallback tree kill, Job Object handles, the 1 s cancel and Manim's interpreter.

The fallback walks a process list with creation times and never kills a
process created before its supposed parent. The Windows helper's answers come
from a stand-in, and Job Object handles from a recording kernel32, so these
tests create no Job Object and start no PowerShell, but for the two marked
``process_table``: one pins the real helper's checked kill, and one cancels a
fake Manim and its child with the kill this process really has. A job that has
a Job Object never asks the process table. Manim runs the real interpreter
behind a venv launcher, a worker that closes waits for its job, and
``process_alive`` works without ``/proc``. Supervisors run in this process
with the ``fake_manim`` stand-in.
"""

from __future__ import annotations

import ctypes
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mathimator_worker import process_group, win32_job
from mathimator_worker import supervisor as supervisor_module
from mathimator_worker.argv import manim_environment, manim_interpreter
from mathimator_worker.process_group import _descendants, _Process, _Stat
from mathimator_worker.supervisor import Supervisor, _Job
from tests.support import REPO, FakeManim, Stdout, complete_clip, video_render

JOURNEY = REPO / "tests" / "fixtures" / "journey-a" / "project.json"
CANCELLED = {"event": "error", "code": "cancelled", "message": "cancelled"}

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects and venv launchers are Windows only")


# The tree the fallback kills


def test_the_tree_is_killed_deepest_first() -> None:
    parents = {10: (1, 100), 20: (10, 110), 21: (10, 111), 30: (20, 120), 40: (30, 130)}
    order = _descendants(10, parents)
    assert sorted(order) == [20, 21, 30, 40]
    for child, (parent, _created) in parents.items():
        if parent in order:
            assert order.index(child) < order.index(parent), order


def test_a_process_older_than_its_parent_is_not_its_child() -> None:
    # The root and two real descendants, then processes whose parent id is stale: one
    # older than the root, as explorer.exe is once userinit.exe has exited, with a child
    # of its own, and one older than the real child that now has its parent's old id.
    parents = {
        500: (4, 1000),
        600: (500, 1001),
        601: (600, 1002),
        77: (500, 900),
        78: (77, 950),
        90: (600, 999),
    }
    assert set(_descendants(500, parents)) == {600, 601}


def test_cycles_and_self_parents_end_the_walk() -> None:
    assert _descendants(10, {10: (20, 100), 20: (10, 100)}) == [20]
    assert _descendants(10, {10: (1, 100), 20: (10, 110), 30: (40, 120), 40: (30, 120)}) == [20]
    assert _descendants(20, {10: (1, 100), 20: (20, 110)}) == []


def test_an_unknown_creation_time_breaks_the_edge() -> None:
    assert _descendants(10, {10: (1, 0), 20: (10, 5)}) == []
    assert _descendants(10, {10: (1, 100), 20: (10, 0)}) == []
    assert _descendants(10, {20: (10, 5)}) == []
    # A child created in the same clock tick as its parent is still its child.
    assert _descendants(10, {10: (1, 100), 20: (10, 100)}) == [20]


def test_one_scan_of_proc_reads_each_parent_group_and_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = " ".join(["0"] * 16)
    stats = {
        "1": f"1 (init) S 0 1 {ticks} 5 0 0",
        # The name is in parentheses and may hold both.
        "42": f"42 (a (b) c) R 1 42 {ticks} 900 0 0",
        "43": "43 (cut) R 1",
    }
    for pid, stat in stats.items():
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "stat").write_text(stat, encoding="utf-8")
    (tmp_path / "44").mkdir()
    (tmp_path / "self").mkdir()
    monkeypatch.setattr(process_group, "_PROC", tmp_path)
    assert process_group._proc_table() == {1: _Stat("S", 0, 1, 5), 42: _Stat("R", 1, 42, 900)}


@pytest.mark.skipif(sys.platform == "win32", reason="Windows asks the process itself")
def test_without_proc_a_process_is_alive_while_a_signal_reaches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_group, "_PROC", tmp_path / "missing")
    proc = _sleeper()
    assert process_group.process_alive(proc.pid)
    proc.kill()
    proc.wait(timeout=10)
    assert not process_group.process_alive(proc.pid)


# The Windows fallback and its helper


def test_the_helper_lists_parents_and_creation_times_and_kills_by_both(monkeypatch: pytest.MonkeyPatch) -> None:
    table = process_group._WindowsProcessTable()
    helper = _Helper(["4\t0\t100\tSystem", "10\t4\t200\tpython.exe", "not a row", "x\t4\t1\tno pid"], [])
    monkeypatch.setattr(table, "_ensure", lambda: helper)
    assert table.rows(fresh=True) == {4: _Process(0, 100, "System"), 10: _Process(4, 200, "python.exe")}
    table.kill([(30, 300), (20, 200)])
    assert helper.commands() == ["PIDS", "KILL 30:300 20:200"]


def test_a_failed_helper_answer_raises_and_the_next_answer_is_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    table = process_group._WindowsProcessTable()
    helper = _Helper(["ERROR Access is denied"], [], ["4\t0\t100\tSystem"])
    monkeypatch.setattr(table, "_ensure", lambda: helper)
    with pytest.raises(OSError, match="Access is denied"):
        table.rows(fresh=True)
    # A live system always lists System and Idle.
    with pytest.raises(OSError, match="no processes"):
        table.rows(fresh=True)
    assert table.rows(fresh=True) == {4: _Process(0, 100, "System")}


@windows_only
@pytest.mark.process_table
def test_the_helper_ends_a_process_only_while_its_creation_time_matches() -> None:
    table = process_group._TABLE
    assert table is not None
    proc = _sleeper()
    try:
        row = table.rows(fresh=True)[proc.pid]
        assert row.ppid == os.getpid()
        assert row.created > 0
        table.kill([(proc.pid, row.created + 1)])
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=0.5)
        table.kill([(proc.pid, row.created)])
        assert proc.wait(timeout=1) == 1
        # A process that has already exited is skipped, not a failure.
        table.kill([(proc.pid, row.created)])
    finally:
        proc.kill()


def test_a_process_the_helper_could_not_end_raises_after_manim_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _sleeper()
    table = process_group._WindowsProcessTable()
    helper = _Helper(
        [f"{proc.pid}\t1\t100\tpython.exe", f"9001\t{proc.pid}\t150\tlatex.exe"],
        ['ERROR Exception calling "Kill" with "1" argument(s): "could not end 9001 (Access is denied)"'],
    )
    monkeypatch.setattr(table, "_ensure", lambda: helper)
    monkeypatch.setattr(process_group, "_TABLE", table)
    monkeypatch.delenv("MATHIMATOR_SKIP_PROCESS_TABLE", raising=False)
    with pytest.raises(OSError, match=r"could not end 9001 \(Access is denied\)"):
        process_group._terminate_windows(proc)
    assert proc.poll() is not None
    assert helper.commands() == ["PIDS", "KILL 9001:150"]


def test_the_fallback_kills_only_what_manim_started_then_manim(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _sleeper()
    table = _Table(
        {
            proc.pid: _Process(1, 100, "python.exe"),
            9001: _Process(proc.pid, 150, "latex.exe"),
            9002: _Process(9001, 160, "kpsewhich.exe"),
            9003: _Process(proc.pid, 50, "explorer.exe"),
        }
    )
    monkeypatch.setattr(process_group, "_TABLE", table)
    monkeypatch.delenv("MATHIMATOR_SKIP_PROCESS_TABLE", raising=False)
    process_group._terminate_windows(proc)
    assert table.killed == [[(9002, 160), (9001, 150)]]
    assert proc.poll() is not None


def test_a_failed_process_table_still_kills_manim(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _sleeper()
    monkeypatch.setattr(process_group, "_TABLE", _Table(OSError("process table helper exited")))
    monkeypatch.delenv("MATHIMATOR_SKIP_PROCESS_TABLE", raising=False)
    with pytest.raises(OSError, match="helper exited"):
        process_group._terminate_windows(proc)
    assert proc.poll() is not None


def test_skipping_the_process_table_never_asks_it(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _sleeper()
    table = _Table(AssertionError("the process table was asked"))
    monkeypatch.setattr(process_group, "_TABLE", table)
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    process_group.warm_process_table()
    process_group._terminate_windows(proc)
    assert proc.poll() is not None
    assert table.killed == []


# Job Object handles


def test_a_cancelled_job_terminates_its_job_object_then_closes_it_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: _Table, stdout: Stdout, fake_manim: FakeManim
) -> None:
    terminated: list[int] = []
    closed: list[int] = []
    monkeypatch.setattr(supervisor_module, "jobs_enabled", lambda: True)
    monkeypatch.setattr(supervisor_module, "assign_kill_on_close", lambda pid: 4242)
    monkeypatch.setattr(supervisor_module, "terminate_job", terminated.append)
    monkeypatch.setattr(supervisor_module, "close_job", closed.append)
    supervisor = _supervisor(tmp_path)
    fake_manim.plan({}, later=[video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)])
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    fake_manim.wait_until_held()
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
    # The stand-in job kills nothing, so the fake Manim ends by itself.
    fake_manim.release()
    _join(job)
    assert stdout.of("preview-1")[-1] == {**CANCELLED, "jobId": "preview-1"}
    assert (terminated, closed) == ([4242], [4242])
    assert table.asked == 0


@windows_only
def test_a_job_object_is_closed_once_when_manim_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kernel32: _Kernel32,
    table: _Table,
    stdout: Stdout,
    fake_manim: FakeManim,
) -> None:
    supervisor = _supervisor(tmp_path)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _send(supervisor, cmd="render_preview", id="preview-1")
    _join(_current(supervisor))
    assert stdout.of("preview-1")[-1]["event"] == "done"
    assert (len(kernel32.jobs), kernel32.terminated) == (1, [])
    assert kernel32.closed_once()
    assert table.asked == 0


@windows_only
def test_a_cancelled_job_object_is_terminated_then_closed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kernel32: _Kernel32,
    table: _Table,
    stdout: Stdout,
    fake_manim: FakeManim,
) -> None:
    supervisor = _supervisor(tmp_path)
    fake_manim.plan({}, later=[video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)])
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    fake_manim.wait_until_held()
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
    # The recording job kills nothing, so the fake Manim ends by itself.
    fake_manim.release()
    _join(job)
    assert stdout.of("preview-1")[-1] == {**CANCELLED, "jobId": "preview-1"}
    assert kernel32.terminated == kernel32.jobs
    assert kernel32.closed_once()
    assert table.asked == 0


@windows_only
def test_a_superseded_job_object_is_terminated_then_closed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kernel32: _Kernel32,
    table: _Table,
    stdout: Stdout,
    fake_manim: FakeManim,
) -> None:
    supervisor = _supervisor(tmp_path)
    fake_manim.plan({}, later=[video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4)])
    _send(supervisor, cmd="render_preview", id="preview-1")
    first = _current(supervisor)
    fake_manim.wait_until_held()
    _send(supervisor, cmd="render_preview", id="preview-2")
    fake_manim.release()
    _join(first)
    # A job closes its handle before it sends its terminal line.
    stdout.wait_for("preview-2", "done")
    assert stdout.of("preview-1")[-1] == {**CANCELLED, "jobId": "preview-1"}
    assert len(kernel32.jobs) == 2
    assert kernel32.terminated == kernel32.jobs[:1]
    assert kernel32.closed_once()
    assert table.asked == 0


@windows_only
def test_a_job_object_that_will_not_close_is_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kernel32: _Kernel32,
    table: _Table,
    stdout: Stdout,
    fake_manim: FakeManim,
) -> None:
    kernel32.failing = {"CloseHandle"}
    supervisor = _supervisor(tmp_path)
    fake_manim.plan(video_render(supervisor.layout, "480p15", [complete_clip(".mp4")] * 4))
    _send(supervisor, cmd="render_preview", id="preview-1")
    _join(_current(supervisor))
    assert stdout.of("preview-1")[-1]["event"] == "done"
    log = supervisor.log_path.read_text(encoding="utf-8")
    assert "job close failed: [WinError 6] CloseHandle failed: The handle is invalid." in log
    assert table.asked == 0


@windows_only
def test_a_failed_job_call_raises_its_windows_error(kernel32: _Kernel32) -> None:
    kernel32.failing = {"TerminateJobObject"}
    with pytest.raises(OSError) as raised:
        win32_job.terminate_job(1000)
    assert raised.value.winerror == 6
    assert str(raised.value) == "[WinError 6] TerminateJobObject failed: The handle is invalid."


def test_closing_the_worker_waits_for_the_running_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "0")
    monkeypatch.setenv("MATHIMATOR_SKIP_PROCESS_TABLE", "1")
    monkeypatch.setattr(supervisor_module, "terminate_process_group", lambda proc: proc.kill())
    supervisor = _supervisor(tmp_path)
    fake_manim.plan({}, later=[{}])
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    fake_manim.wait_until_held()
    supervisor.close()
    # serve() closes at the end of stdin; nothing the job started is left running.
    assert not job.thread.is_alive()
    assert job.proc is not None and job.proc.poll() is not None
    assert stdout.of("preview-1")[-1] == {**CANCELLED, "jobId": "preview-1"}


@pytest.mark.process_table
def test_a_cancel_ends_manim_and_its_tex_child_within_one_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: Stdout, fake_manim: FakeManim
) -> None:
    """Spec section 2's 1 s cancel, with no real Manim: the fake runs a child that sleeps as a TeX run would.

    The kill is whichever this process has: the Job Object, the process table or the process group.
    """
    monkeypatch.delenv("MATHIMATOR_SKIP_PROCESS_TABLE", raising=False)
    supervisor = _supervisor(tmp_path)
    fake_manim.plan({}, later=[{}], child=True)
    _send(supervisor, cmd="render_preview", id="preview-1")
    job = _current(supervisor)
    fake_manim.wait_until_held()
    if not win32_job.jobs_enabled():
        # Without a Job Object the kill walks the process table, whose helper starts beside Manim.
        process_group.warm_process_table()
    assert job.proc is not None
    watched = [job.proc.pid, fake_manim.child_pid()]
    assert all(process_group.process_alive(pid) for pid in watched)
    cancelled_at = time.monotonic()
    _send(supervisor, cmd="cancel", id="cancel-1", jobId="preview-1")
    while any(process_group.process_alive(pid) for pid in watched) and time.monotonic() - cancelled_at < 1:
        time.sleep(0.01)
    assert not [pid for pid in watched if process_group.process_alive(pid)]
    _join(job)
    assert stdout.of("preview-1")[-1] == {**CANCELLED, "jobId": "preview-1"}


# Manim's interpreter


@windows_only
def test_manim_runs_the_real_interpreter_behind_the_workers_venv_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "venv" / "Scripts" / "python.exe"
    other = tmp_path / "python" / "python.exe"
    for path in (launcher, other):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(launcher))
    monkeypatch.setattr(sys, "_base_executable", str(other), raising=False)
    assert manim_interpreter(str(launcher)) == (str(other), str(launcher))
    # Another interpreter, such as the installed app's real one, runs as it is.
    assert manim_interpreter(str(other)) == (str(other), None)
    assert manim_interpreter(str(tmp_path / "missing.exe")) == (str(tmp_path / "missing.exe"), None)


def test_an_interpreter_outside_a_venv_runs_manim_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "_base_executable", sys.executable, raising=False)
    assert manim_interpreter(sys.executable) == (sys.executable, None)


def test_the_venv_launcher_is_named_to_the_real_interpreter(tmp_path: Path) -> None:
    launcher = str(tmp_path / "venv" / "Scripts" / "python.exe")
    assert manim_environment({}, tmp_path, launcher)["__PYVENV_LAUNCHER__"] == launcher
    assert "__PYVENV_LAUNCHER__" not in manim_environment({}, tmp_path)


class _Helper:
    """The process table helper: answers each command with the next of ``answers`` and records the commands."""

    def __init__(self, *answers: list[str]) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join(f"{line}\n" for answer in answers for line in [*answer, "___END___"]))
        self.stderr = None

    def commands(self) -> list[str]:
        return self.stdin.getvalue().splitlines()


class _Table:
    """A process table that lists ``rows``, or raises them, and records how often it is asked and what it kills."""

    def __init__(self, rows: dict[int, _Process] | Exception) -> None:
        self._rows = rows
        self.asked = 0
        self.killed: list[list[tuple[int, int]]] = []

    def rows(self, *, fresh: bool) -> dict[int, _Process]:
        self.asked += 1
        if isinstance(self._rows, Exception):
            raise self._rows
        return dict(self._rows)

    def kill(self, victims: list[tuple[int, int]]) -> None:
        self.killed.append(victims)


class _Kernel32:
    """The kernel32 calls of the Job Object binding, recorded instead of made.

    Each handle it gives out is a new number. A call named in ``failing``
    fails with ERROR_INVALID_HANDLE.
    """

    def __init__(self) -> None:
        self.jobs: list[int] = []
        self.processes: list[int] = []
        self.terminated: list[int] = []
        self.closed: list[int] = []
        self.failing: set[str] = set()

    def CreateJobObjectW(self, attributes: object, name: object) -> int:
        self.jobs.append(self._handle())
        return self.jobs[-1]

    def SetInformationJobObject(self, job: int, kind: int, info: object, size: int) -> bool:
        return True

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
        self.processes.append(self._handle())
        return self.processes[-1]

    def AssignProcessToJobObject(self, job: int, process: int) -> bool:
        return True

    def TerminateJobObject(self, job: int, code: int) -> bool:
        self.terminated.append(job)
        return self._result("TerminateJobObject")

    def CloseHandle(self, handle: int) -> bool:
        self.closed.append(handle)
        return self._result("CloseHandle")

    def closed_once(self) -> bool:
        """Whether every handle given out was closed exactly once."""
        return sorted(self.closed) == sorted(self.jobs + self.processes)

    def _handle(self) -> int:
        return 1000 + len(self.jobs) + len(self.processes)

    def _result(self, call: str) -> bool:
        if call in self.failing:
            ctypes.set_last_error(6)
            return False
        return True


@pytest.fixture
def kernel32(monkeypatch: pytest.MonkeyPatch) -> _Kernel32:
    """Job Objects on, made by a recording kernel32."""
    kernel = _Kernel32()
    monkeypatch.setenv("MATHIMATOR_WIN32_JOBS", "1")
    monkeypatch.setattr(win32_job, "_kernel32", lambda: kernel)
    return kernel


@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch) -> _Table:
    """The process table, not skipped, as a stand-in that lists nothing, so a test can show it was never asked."""
    table = _Table({})
    monkeypatch.setattr(process_group, "_TABLE", table)
    monkeypatch.delenv("MATHIMATOR_SKIP_PROCESS_TABLE", raising=False)
    return table


def _supervisor(tmp_path: Path) -> Supervisor:
    """A supervisor bound to a copy of Journey A."""
    project = tmp_path / "journey"
    project.mkdir()
    (project / "project.json").write_bytes(JOURNEY.read_bytes())
    return Supervisor(project, sys.executable, tmp_path / "journey.log", False)


def _send(supervisor: Supervisor, **request: object) -> None:
    supervisor.handle_line(json.dumps(request))


def _current(supervisor: Supervisor) -> _Job:
    job = supervisor._current
    assert job is not None
    return job


def _join(job: _Job) -> None:
    job.thread.join(timeout=60)
    assert not job.thread.is_alive()


def _sleeper() -> subprocess.Popen[str]:
    """A process that sleeps for a minute: the real interpreter, so killing it leaves nothing behind."""
    python: str = getattr(sys, "_base_executable", sys.executable)
    return subprocess.Popen([python, "-c", "import time; time.sleep(60)"], text=True)
