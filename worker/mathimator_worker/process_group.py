"""Stop a Manim process and every process it started, when no Job Object does.

On Windows each Manim normally runs in a kill-on-close Job Object
(``win32_job``). This module is the fallback when Job Objects are off
(``MATHIMATOR_WIN32_JOBS=0``) or assigning or terminating one fails. A hidden
PowerShell helper lists every process on the system with its parent and its
creation time. It starts with the first job that runs without a Job Object,
never when the worker starts (D15). Windows keeps the parent id of a process
whose parent has exited, and gives that id to new processes, so a process
counts as a child only when it was created no earlier than its parent, and
the helper ends one only while a handle shows it is still the process it
listed (D15, F024). With ``MATHIMATOR_SKIP_PROCESS_TABLE=1`` the helper never
starts and the fallback ends Manim alone.

Linux keeps the phase 1 mechanism: Manim leads a new session, cancel kills its
process group, and one scan of ``/proc`` finds a TeX helper that left the
group. macOS is untested (D47). It has no ``/proc``, so only the group is
killed there.

``process_alive``, ``process_name`` and ``descendant_pids`` are for the tests.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import NamedTuple

if sys.platform == "win32":
    import _winapi
else:
    _winapi = None

# The OpenProcess access that reads an exit code, and the exit code of a process that still runs.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259

_PROC = Path("/proc")

_HELPER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Text;
using System.Runtime.InteropServices;
public static class MathimatorProc {
    const uint SNAPPROCESS = 0x00000002;
    const uint TERMINATE = 0x0001;
    const uint QUERY_LIMITED_INFORMATION = 0x1000;
    const uint STILL_ACTIVE = 259;
    const int ERROR_INVALID_PARAMETER = 87;
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct PROCESSENTRY32 {
        public uint dwSize;
        public uint cntUsage;
        public uint th32ProcessID;
        public UIntPtr th32DefaultHeapID;
        public uint th32ModuleID;
        public uint cntThreads;
        public uint th32ParentProcessID;
        public int pcPriClassBase;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
        public string szExeFile;
    }
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern IntPtr CreateToolhelp32Snapshot(uint dwFlags, uint th32ProcessID);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool Process32First(IntPtr hSnapshot, ref PROCESSENTRY32 lppe);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool Process32Next(IntPtr hSnapshot, ref PROCESSENTRY32 lppe);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr hObject);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetProcessTimes(
        IntPtr process, out long created, out long exited, out long kernel, out long user);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    static string Clean(string value) {
        if (value == null) return "";
        return value.Replace("\t", " ").Replace("\r", " ").Replace("\n", " ");
    }
    // The creation time of an open process as a FILETIME, or 0 when it cannot be read.
    static long Created(IntPtr process) {
        long created, exited, kernel, user;
        return GetProcessTimes(process, out created, out exited, out kernel, out user) ? created : 0;
    }
    static long Created(uint pid) {
        IntPtr process = OpenProcess(QUERY_LIMITED_INFORMATION, false, pid);
        if (process == IntPtr.Zero) return 0;
        try {
            return Created(process);
        } finally {
            CloseHandle(process);
        }
    }
    // Whether an open process has not exited. One whose exit code cannot be read counts as running.
    static bool Running(IntPtr process) {
        uint code;
        return !GetExitCodeProcess(process, out code) || code == STILL_ACTIVE;
    }
    // Every process as one Toolhelp snapshot lists it.
    static List<PROCESSENTRY32> Processes() {
        IntPtr snap = CreateToolhelp32Snapshot(SNAPPROCESS, 0);
        if (snap == IntPtr.Zero || snap == new IntPtr(-1)) {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }
        try {
            var entry = new PROCESSENTRY32();
            entry.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
            if (!Process32First(snap, ref entry)) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
            var processes = new List<PROCESSENTRY32>();
            do {
                processes.Add(entry);
            } while (Process32Next(snap, ref entry));
            return processes;
        } finally {
            CloseHandle(snap);
        }
    }
    // One line per process: its pid, its parent's pid, its creation time and its executable.
    // The times are read by pid after the snapshot, so a second snapshot checks each pid: one
    // it lists under another parent went to a new process in between, and its time is written as 0.
    public static string Snapshot() {
        List<PROCESSENTRY32> processes = Processes();
        var created = new long[processes.Count];
        for (int i = 0; i < processes.Count; i++) created[i] = Created(processes[i].th32ProcessID);
        var parents = new Dictionary<uint, uint>();
        foreach (PROCESSENTRY32 entry in Processes()) parents[entry.th32ProcessID] = entry.th32ParentProcessID;
        var builder = new StringBuilder();
        for (int i = 0; i < processes.Count; i++) {
            PROCESSENTRY32 entry = processes[i];
            uint parent;
            bool reused = parents.TryGetValue(entry.th32ProcessID, out parent) && parent != entry.th32ParentProcessID;
            builder.Append(entry.th32ProcessID).Append('\t');
            builder.Append(entry.th32ParentProcessID).Append('\t');
            builder.Append(reused ? 0 : created[i]).Append('\t');
            builder.Append(Clean(entry.szExeFile)).Append('\n');
        }
        return builder.ToString();
    }
    // Ends each "pid:created" in order, while that pid is still the process created then.
    // The check and the kill use one handle, so a pid reused in between is never hit.
    // A process already gone is skipped. One that could not be ended fails the command once the rest are tried.
    public static void Kill(string victims) {
        var failures = new List<string>();
        foreach (string victim in victims.Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries)) {
            string[] parts = victim.Split(':');
            IntPtr process = OpenProcess(TERMINATE | QUERY_LIMITED_INFORMATION, false, uint.Parse(parts[0]));
            if (process == IntPtr.Zero) {
                // ERROR_INVALID_PARAMETER: no process has this pid any more.
                int error = Marshal.GetLastWin32Error();
                if (error != ERROR_INVALID_PARAMETER) failures.Add(Failure(parts[0], error));
                continue;
            }
            try {
                if (Created(process) == long.Parse(parts[1]) && !TerminateProcess(process, 1)) {
                    int error = Marshal.GetLastWin32Error();
                    // Ending a process that has already exited fails too.
                    if (Running(process)) failures.Add(Failure(parts[0], error));
                }
            } finally {
                CloseHandle(process);
            }
        }
        if (failures.Count > 0) {
            throw new InvalidOperationException("could not end " + string.Join(", ", failures));
        }
    }
    static string Failure(string pid, int error) {
        return pid + " (" + new System.ComponentModel.Win32Exception(error).Message + ")";
    }
}
'@
while ($true) {
    $cmd = [Console]::In.ReadLine()
    if ($null -eq $cmd -or $cmd -eq 'EXIT') { break }
    try {
        if ($cmd -eq 'PIDS') {
            $text = [MathimatorProc]::Snapshot()
            if ($text) { Write-Output $text }
        } elseif ($cmd.StartsWith('KILL ')) {
            [MathimatorProc]::Kill($cmd.Substring(5))
        }
        Write-Output '___END___'
    } catch {
        Write-Output ('ERROR ' + $_.Exception.Message)
        Write-Output '___END___'
    }
}
"""


class _Process(NamedTuple):
    """A process the Windows helper listed."""

    ppid: int
    # A FILETIME, or 0 when the helper could not read it or the pid went to another process meanwhile.
    created: int
    name: str


class _Stat(NamedTuple):
    """The fields of ``/proc/<pid>/stat`` the kill reads."""

    state: str
    ppid: int
    pgrp: int
    # Clock ticks after boot.
    created: int


class _WindowsProcessTable:
    """Every process on the system, listed by the PowerShell helper, which also does the checked kill."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._script: Path | None = None
        self._rows: dict[int, _Process] = {}
        self._rows_at = 0.0

    def rows(self, *, fresh: bool) -> dict[int, _Process]:
        """Each process by its pid. Unless ``fresh``, a list taken in the last 0.1 s will do."""
        with self._lock:
            now = time.monotonic()
            if not fresh and self._rows and now - self._rows_at < 0.1:
                return dict(self._rows)
            rows: dict[int, _Process] = {}
            for line in self._ask("PIDS"):
                parts = line.split("\t", 3)
                if len(parts) < 4:
                    continue
                try:
                    pid, ppid, created = int(parts[0]), int(parts[1]), int(parts[2])
                except ValueError:
                    continue
                rows[pid] = _Process(ppid, created, parts[3].strip())
            if not rows:
                # A live system always lists System and Idle, so an empty table is a failed snapshot (F098).
                raise OSError("process table helper listed no processes")
            self._rows = rows
            self._rows_at = now
            return dict(rows)

    def kill(self, victims: list[tuple[int, int]]) -> None:
        """End each ``(pid, created)`` in order, while ``pid`` is still the process created then (D15).

        Raises OSError naming each process that still runs because it could not be ended (F098).
        """
        with self._lock:
            self._ask("KILL " + " ".join(f"{pid}:{created}" for pid, created in victims))

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is not None and proc.poll() is None:
                if proc.stdin is not None:
                    with suppress(OSError):
                        proc.stdin.write("EXIT\n")
                        proc.stdin.flush()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            if self._script is not None:
                with suppress(OSError):
                    self._script.unlink(missing_ok=True)
                self._script = None

    def _ask(self, command: str) -> list[str]:
        """Send ``command`` and return the lines of the answer. An ``ERROR`` line raises OSError (F098)."""
        proc = self._ensure()
        assert proc.stdin is not None
        assert proc.stdout is not None
        try:
            proc.stdin.write(command + "\n")
            proc.stdin.flush()
        except OSError as exc:
            self._proc = None
            raise OSError(f"process table helper stopped: {exc}") from exc
        lines: list[str] = []
        while True:
            line = proc.stdout.readline()
            if line == "":
                self._proc = None
                detail = ""
                if proc.stderr is not None:
                    detail = proc.stderr.read()[:500]
                raise OSError(f"process table helper exited: {detail}")
            text = line.strip("\r\n").lstrip("\ufeff")
            if text == "___END___":
                break
            if text:
                lines.append(text)
        # Only now, so the next answer starts after this one's end line.
        for text in lines:
            if text.startswith("ERROR "):
                raise OSError(f"process table helper failed: {text[6:]}")
        return lines

    def _ensure(self) -> subprocess.Popen[str]:
        if sys.platform != "win32":
            raise OSError("the process table helper runs only on Windows")
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        # Named for this worker, so two workers never rewrite each other's script (F027).
        self._script = Path(tempfile.gettempdir()) / f"mathimator-proc-table-{os.getpid()}.ps1"
        self._script.write_text(_HELPER_SCRIPT, encoding="utf-8")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        self._proc = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self._script),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startup,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return self._proc


_TABLE = _WindowsProcessTable() if sys.platform == "win32" else None


def _close_table() -> None:
    if _TABLE is not None:
        _TABLE.close()


atexit.register(_close_table)


def _table() -> _WindowsProcessTable | None:
    """The Windows process table, or None off Windows and while ``MATHIMATOR_SKIP_PROCESS_TABLE=1`` (F028)."""
    if os.environ.get("MATHIMATOR_SKIP_PROCESS_TABLE") == "1":
        return None
    return _TABLE


def warm_process_table() -> None:
    """Start the Windows process table helper, so a cancel without a Job Object does not wait for it."""
    table = _table()
    if table is not None:
        table.rows(fresh=True)


def terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Kill ``proc`` and the processes it started, never one created before its parent (D15).

    On Windows a failed process table, or a process the helper could not
    end, raises OSError after ``proc`` itself is killed (F025, F098).
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        _terminate_windows(proc)
        return
    pid = proc.pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    table = _proc_table()
    victims = {pid, *(other for other, stat in table.items() if stat.pgrp == pgid)}
    victims.update(_descendants(pid, {other: (stat.ppid, stat.created) for other, stat in table.items()}))
    with suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)
    for victim in victims:
        with suppress(ProcessLookupError):
            os.kill(victim, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=1)


def process_alive(pid: int) -> bool:
    """Whether ``pid`` still runs and is not a zombie. For the tests.

    Without ``/proc``, as on macOS, every process ``os.kill(pid, 0)`` reaches
    counts, zombies too (D47).
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_process_alive(pid)
    if not _PROC.is_dir():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    stat = _read_stat(pid)
    return stat is not None and stat.state != "Z"


def process_name(pid: int) -> str:
    """Executable name without a trailing ``.exe``, lowercased on Windows. For the tests."""
    if sys.platform == "win32":
        table = _table()
        row = None if table is None else table.rows(fresh=False).get(pid)
        if row is None:
            return ""
        stem = row.name[:-4] if row.name.lower().endswith(".exe") else row.name
        return stem.lower()
    try:
        return (_PROC / str(pid) / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def descendant_pids(root: int) -> set[int]:
    """Every process below ``root``, by the rules the kill follows. For the tests."""
    if sys.platform == "win32":
        table = _table()
        rows = {} if table is None else table.rows(fresh=False)
        return set(_descendants(root, {pid: (row.ppid, row.created) for pid, row in rows.items()}))
    return set(_descendants(root, {pid: (stat.ppid, stat.created) for pid, stat in _proc_table().items()}))


def _terminate_windows(proc: subprocess.Popen[str]) -> None:
    """Kill what ``proc`` started, deepest first, then ``proc`` itself through the handle Popen holds."""
    table = _table()
    failure: OSError | None = None
    if table is not None:
        try:
            rows = table.rows(fresh=True)
            victims = _descendants(proc.pid, {pid: (row.ppid, row.created) for pid, row in rows.items()})
            if victims:
                table.kill([(pid, rows[pid].created) for pid in victims])
        except OSError as exc:
            failure = exc
    proc.terminate()
    with suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=1)
    if failure is not None:
        raise failure


def _descendants(root: int, parents: Mapping[int, tuple[int, int]]) -> list[int]:
    """The processes below ``root``, deepest first. ``parents`` maps a pid to its parent's pid and its creation time.

    A process is a child of its parent only when both creation times are known
    and it was created no earlier than the parent (D15). Windows keeps the
    parent id of a process whose parent has exited and reuses ids, so a
    long-running process could otherwise pass for a child of a new one.
    """
    children: dict[int, list[int]] = {}
    for pid, (ppid, _created) in parents.items():
        children.setdefault(ppid, []).append(pid)
    depth: dict[int, int] = {}
    stack = [root]
    while stack:
        parent = stack.pop()
        born = parents.get(parent, (0, 0))[1]
        for child in children.get(parent, []):
            if child == root or child in depth or not 0 < born <= parents[child][1]:
                continue
            depth[child] = depth.get(parent, 0) + 1
            stack.append(child)
    return sorted(depth, key=depth.__getitem__, reverse=True)


def _windows_process_alive(pid: int) -> bool:
    if _winapi is None:
        return False
    try:
        handle = _winapi.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except OSError:
        return False
    try:
        code = _winapi.GetExitCodeProcess(handle)
    except OSError:
        return False
    finally:
        _winapi.CloseHandle(handle)
    return code == STILL_ACTIVE


def _proc_table() -> dict[int, _Stat]:
    """Every process ``/proc`` lists, in one scan. Empty without ``/proc``."""
    table: dict[int, _Stat] = {}
    if not _PROC.is_dir():
        return table
    for entry in _PROC.iterdir():
        if entry.name.isdigit() and (stat := _read_stat(int(entry.name))) is not None:
            table[int(entry.name)] = stat
    return table


def _read_stat(pid: int) -> _Stat | None:
    """``/proc/<pid>/stat``, or None when it cannot be read."""
    try:
        text = (_PROC / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        # The name before these fields is in parentheses and may hold spaces and parentheses itself.
        fields = text.rsplit(")", 1)[1].split()
        return _Stat(fields[0], int(fields[1]), int(fields[2]), int(fields[19]))
    except (IndexError, ValueError):
        return None
