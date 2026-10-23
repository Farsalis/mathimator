"""Windows Job Object teardown for a Manim grandchild.

Craig approved this binding: on Windows each Manim process is assigned to a
job with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. Cancel calls
``TerminateJobObject``, which also ends ``latex`` and ``dvisvgm``. Linux and
macOS keep the process-group path in ``process_group``.

On Windows the job is the default. ``MATHIMATOR_WIN32_JOBS=0`` turns it off.
The tests that create a job are skipped unless ``MATHIMATOR_WIN32_TESTS=1``.
"""

from __future__ import annotations

import os
import ctypes
from ctypes import wintypes

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100


def jobs_enabled() -> bool:
    """True when this Windows process should call the Job Object APIs.

    Unset means on. ``MATHIMATOR_WIN32_JOBS=0`` keeps the process-group path
    so a suite can run without calling the API.
    """
    if os.name != "nt":
        return False
    return os.environ.get("MATHIMATOR_WIN32_JOBS") != "0"


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("_pad0", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("_pad1", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def assign_kill_on_close(pid: int) -> int:
    """Put ``pid`` in a new kill-on-close job. Returns the job handle.

    The caller keeps the handle alive. Closing it, or calling
    ``terminate_job``, ends the process and every process it created.
    """
    if not jobs_enabled():
        raise OSError("Job Object assignment is disabled")
    if pid <= 0:
        raise OSError(f"invalid pid {pid}")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise OSError(error, "SetInformationJobObject failed")
    process = kernel.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    if not process:
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise OSError(error, "OpenProcess failed")
    assigned = kernel.AssignProcessToJobObject(job, process)
    kernel.CloseHandle(process)
    if not assigned:
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise OSError(error, "AssignProcessToJobObject failed")
    return int(job)


def terminate_job(handle: int) -> None:
    """End every process in the job, then close the handle."""
    if not jobs_enabled():
        raise OSError("Job Object termination is disabled")
    if handle <= 0:
        raise OSError("invalid job handle")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = wintypes.HANDLE(handle)
    if not kernel.TerminateJobObject(job, 1):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise OSError(error, "TerminateJobObject failed")
    kernel.CloseHandle(job)


def close_job(handle: int) -> None:
    """Close a job handle. Kill-on-close then reaps anything still inside."""
    if handle <= 0:
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CloseHandle(wintypes.HANDLE(handle))
