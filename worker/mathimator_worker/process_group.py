"""Stop a grandchild and the processes it has spawned.

Unix keeps the phase 1 mechanism: a new session, ``killpg``, and ``/proc`` so a
TeX helper that left the group is still found. Windows ignores
``start_new_session`` and has no ``/proc``. The Windows path asks a helper
process, through ``subprocess``, for parent/child ids and then stops each one
with ``os.kill``. That stays inside Python's process APIs.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path

if os.name == "nt":
    import _winapi
else:
    _winapi = None  # type: ignore[assignment]

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
using System.Text;
using System.Runtime.InteropServices;
public static class MathimatorProc {
    const uint SNAPPROCESS = 0x00000002;
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
    static extern bool ReadProcessMemory(IntPtr process, IntPtr address, byte[] buffer, IntPtr size, out IntPtr read);
    [DllImport("ntdll.dll")]
    static extern int NtQueryInformationProcess(
        IntPtr process, int klass, byte[] information, int length, out int returned);
    static string Clean(string value) {
        if (value == null) return "";
        return value.Replace("\t", " ").Replace("\r", " ").Replace("\n", " ");
    }
    public static string CommandLine(uint pid) {
        IntPtr handle = OpenProcess(0x0410, false, pid);
        if (handle == IntPtr.Zero) return "";
        try {
            byte[] basic = new byte[48];
            int returned;
            if (NtQueryInformationProcess(handle, 0, basic, basic.Length, out returned) != 0) return "";
            long peb = BitConverter.ToInt64(basic, 8);
            if (peb == 0) return "";
            byte[] parameters = new byte[8];
            IntPtr read;
            if (!ReadProcessMemory(handle, new IntPtr(peb + 0x20), parameters, new IntPtr(8), out read)) return "";
            long block = BitConverter.ToInt64(parameters, 0);
            if (block == 0) return "";
            byte[] text = new byte[16];
            if (!ReadProcessMemory(handle, new IntPtr(block + 0x70), text, new IntPtr(16), out read)) return "";
            int length = BitConverter.ToUInt16(text, 0);
            long buffer = BitConverter.ToInt64(text, 8);
            if (length <= 0 || length > 32768 || buffer == 0) return "";
            byte[] chars = new byte[length];
            if (!ReadProcessMemory(handle, new IntPtr(buffer), chars, new IntPtr(length), out read)) return "";
            return Clean(Encoding.Unicode.GetString(chars));
        } catch (Exception) {
            return "";
        } finally {
            CloseHandle(handle);
        }
    }
    public static string Snapshot() {
        IntPtr snap = CreateToolhelp32Snapshot(SNAPPROCESS, 0);
        if (snap == IntPtr.Zero || snap == new IntPtr(-1)) {
            return "";
        }
        try {
            var entry = new PROCESSENTRY32();
            entry.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
            if (!Process32First(snap, ref entry)) {
                return "";
            }
            var builder = new StringBuilder();
            do {
                string name = Clean(entry.szExeFile);
                builder.Append(entry.th32ProcessID);
                builder.Append('\t');
                builder.Append(entry.th32ParentProcessID);
                builder.Append('\t');
                builder.Append(name);
                builder.Append('\t');
                builder.Append(CommandLine(entry.th32ProcessID));
                builder.Append('\n');
            } while (Process32Next(snap, ref entry));
            return builder.ToString();
        } finally {
            CloseHandle(snap);
        }
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
            Write-Output '___END___'
        } else {
            Write-Output '___END___'
        }
    } catch {
        Write-Output ('ERROR ' + $_.Exception.Message)
        Write-Output '___END___'
    }
}
"""


class _WindowsProcessTable:
    """Parent map and command lines for the current user session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._pids: list[tuple[int, int, str, str]] = []
        self._pids_at = 0.0

    def pids(self, *, fresh: bool) -> list[tuple[int, int, str, str]]:
        with self._lock:
            now = time.monotonic()
            if not fresh and self._pids and now - self._pids_at < 0.1:
                return list(self._pids)
            lines = self._ask("PIDS")
            rows: list[tuple[int, int, str, str]] = []
            for line in lines:
                parts = line.split("\t", 3)
                if len(parts) < 3:
                    continue
                try:
                    pid = int(parts[0])
                    ppid = int(parts[1])
                except ValueError:
                    continue
                name = parts[2].strip()
                command = parts[3].strip() if len(parts) == 4 else ""
                rows.append((pid, ppid, name, command))
            self._pids = rows
            self._pids_at = now
            return list(rows)

    def command_line(self, pid: int) -> str:
        for row_pid, _ppid, _name, command in self.pids(fresh=False):
            if row_pid == pid:
                return command
        return ""

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None or proc.poll() is not None:
                return
            if proc.stdin is not None:
                try:
                    proc.stdin.write("EXIT\n")
                    proc.stdin.flush()
                except OSError:
                    pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)

    def _ask(self, command: str) -> list[str]:
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
                return lines
            if text:
                lines.append(text)

    def _ensure(self) -> subprocess.Popen[str]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        script = Path(os.environ.get("TEMP", ".")) / "mathimator-proc-snap-v1.ps1"
        script.write_text(_HELPER_SCRIPT, encoding="utf-8")
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
                str(script),
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


_TABLE = _WindowsProcessTable() if os.name == "nt" else None


def _close_table() -> None:
    if _TABLE is not None:
        _TABLE.close()


atexit.register(_close_table)


def warm_process_table() -> None:
    """Start the Windows process table helper before a timed cancel."""
    if os.environ.get("MATHIMATOR_SKIP_PROCESS_TABLE") == "1":
        return
    if _TABLE is None:
        return
    _TABLE.pids(fresh=True)


def terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Kill ``proc`` and its descendants, then reap what this process can reap."""
    if proc.poll() is not None:
        _reap_zombies([proc.pid])
        return

    if os.name == "nt":
        _terminate_windows(proc)
        return

    pid = proc.pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return

    victims = {pid}
    victims.update(_pids_in_group(pgid))
    victims.update(_descendant_pids(pid))

    if hasattr(os, "killpg"):
        with suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
    for victim in victims:
        with suppress(ProcessLookupError):
            os.kill(victim, signal.SIGKILL)

    with suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=1)
    _reap_zombies(victims)


def process_alive(pid: int) -> bool:
    """Return whether ``pid`` still exists and is not a zombie."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    stat = _read_stat(pid)
    if stat is None:
        return False
    return stat[0] != "Z"


def process_name(pid: int) -> str:
    """Executable name without a trailing ``.exe``, lowercased on Windows."""
    if os.name == "nt":
        if _TABLE is None:
            return ""
        for row_pid, _ppid, name, _command in _TABLE.pids(fresh=False):
            if row_pid == pid:
                stem = name[:-4] if name.lower().endswith(".exe") else name
                return stem.lower()
        return ""
    path = Path("/proc") / str(pid) / "comm"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def process_command_line(pid: int) -> str:
    """Return the command line, or an empty string when it cannot be read."""
    if os.name == "nt":
        if _TABLE is None:
            return ""
        return _TABLE.command_line(pid)
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _terminate_windows(proc: subprocess.Popen[str]) -> None:
    pid = proc.pid
    if _TABLE is None:
        _terminate_pid(pid)
    else:
        rows = _TABLE.pids(fresh=True)
        parents = {row_pid: ppid for row_pid, ppid, _name, _command in rows}
        victims = _descendants_from_parents(pid, parents)
        victims.add(pid)
        for victim in _deepest_first(pid, victims, parents):
            _terminate_pid(victim)
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        _terminate_pid(pid)


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def _windows_process_alive(pid: int) -> bool:
    if _winapi is None:
        return False
    access = 0x1000
    still_active = 259
    try:
        handle = _winapi.OpenProcess(access, False, pid)
    except OSError:
        return False
    try:
        code = _winapi.GetExitCodeProcess(handle)
    except OSError:
        return False
    finally:
        _winapi.CloseHandle(handle)
    return code == still_active


def _descendants_from_parents(root: int, parents: dict[int, int]) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    found: set[int] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child in found or child == root:
                continue
            found.add(child)
            stack.append(child)
    return found


def _deepest_first(root: int, victims: set[int], parents: dict[int, int]) -> list[int]:
    depth = {root: 0}
    for pid in victims:
        cursor = pid
        seen: list[int] = []
        while cursor not in depth and cursor in parents and cursor not in seen:
            seen.append(cursor)
            cursor = parents[cursor]
        base = depth.get(cursor, 0)
        for offset, item in enumerate(reversed(seen), start=1):
            depth[item] = base + offset
    ordered = [pid for pid in victims if pid != root]
    ordered.sort(key=lambda item: depth.get(item, 0), reverse=True)
    ordered.append(root)
    return ordered


def _reap_zombies(pids: set[int] | list[int]) -> None:
    if os.name == "nt" or not hasattr(os, "waitpid"):
        return
    for pid in pids:
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError, PermissionError):
            continue


def _pids_in_group(pgid: int) -> set[int]:
    found: set[int] = set()
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return found
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        parsed = _read_stat(int(entry.name))
        if parsed is None:
            continue
        _state, _ppid, pgrp = parsed
        if pgrp == pgid:
            found.add(int(entry.name))
    return found


def _descendant_pids(root: int) -> set[int]:
    if os.name == "nt":
        if _TABLE is None:
            return set()
        rows = _TABLE.pids(fresh=False)
        parents = {pid: ppid for pid, ppid, _name, _command in rows}
        return _descendants_from_parents(root, parents)
    children: dict[int, list[int]] = {}
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return set()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        parsed = _read_stat(pid)
        if parsed is None:
            continue
        _state, ppid, _pgrp = parsed
        children.setdefault(ppid, []).append(pid)
    found: set[int] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child in found:
                continue
            found.add(child)
            stack.append(child)
    return found


def _read_stat(pid: int) -> tuple[str, int, int] | None:
    """Return ``(state, ppid, pgrp)`` from ``/proc/<pid>/stat``."""
    path = Path("/proc") / str(pid) / "stat"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        rest = text.rsplit(")", 1)[1].split()
        state = rest[0]
        ppid = int(rest[1])
        pgrp = int(rest[2])
    except (IndexError, ValueError):
        return None
    return state, ppid, pgrp
