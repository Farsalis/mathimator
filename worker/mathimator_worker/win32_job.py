"""Windows Job Object teardown for a Manim grandchild.

On Windows each Manim process is assigned to a job with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. Cancel calls
``TerminateJobObject``, which also ends ``latex`` and ``dvisvgm``. When no job
can be used, ``process_group`` stops the tree instead.

On Windows the job is the default. ``MATHIMATOR_WIN32_JOBS=0`` turns it off.
The tests that create a job are skipped unless ``MATHIMATOR_WIN32_TESTS=1``.

A job handle has one owner, which may terminate the job any number of times
and closes the handle once. A failed call raises the OSError of its
Windows error code.
"""

from __future__ import annotations

import ctypes
import functools
import os
import sys
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
    if sys.platform != "win32":
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


@functools.cache
def _kernel32() -> ctypes.CDLL:
    """kernel32 with the prototype of every call this module makes, bound once."""
    if sys.platform != "win32":
        raise OSError("Job Objects exist only on Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _failed(name: str) -> OSError:
    """The error of the kernel32 call ``name``, which has just failed: ``[WinError <code>] <name> failed: ...``."""
    if sys.platform != "win32":
        raise OSError("Job Objects exist only on Windows")
    code = ctypes.get_last_error()
    return ctypes.WinError(code, f"{name} failed: {ctypes.FormatError(code)}")


def assign_kill_on_close(pid: int) -> int:
    """Put ``pid`` in a new kill-on-close job. Returns the job handle.

    The caller owns the handle. ``terminate_job`` ends the process and every
    process it created, and so does closing the handle with ``close_job``.
    """
    if sys.platform != "win32" or not jobs_enabled():
        raise OSError("Job Object assignment is disabled")
    if pid <= 0:
        raise OSError(f"invalid pid {pid}")
    kernel = _kernel32()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _failed("CreateJobObjectW")
    try:
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise _failed("SetInformationJobObject")
        process = kernel.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not process:
            raise _failed("OpenProcess")
        try:
            if not kernel.AssignProcessToJobObject(job, process):
                raise _failed("AssignProcessToJobObject")
        finally:
            kernel.CloseHandle(process)
    except OSError:
        kernel.CloseHandle(job)
        raise
    return int(job)


def terminate_job(handle: int) -> None:
    """End every process in the job. The handle stays open for its owner to close once with ``close_job``."""
    if not _kernel32().TerminateJobObject(handle, 1):
        raise _failed("TerminateJobObject")


def close_job(handle: int) -> None:
    """Close a job handle. Kill-on-close then ends whatever is still inside."""
    if not _kernel32().CloseHandle(handle):
        raise _failed("CloseHandle")
