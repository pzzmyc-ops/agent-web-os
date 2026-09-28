from __future__ import annotations

import ctypes
import subprocess
import threading
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
CREATE_SUSPENDED = 0x00000004
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_SUSPEND_RESUME = 0x0800


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
_kernel32.SetInformationJobObject.restype = wintypes.BOOL
_kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateJobObject.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtResumeProcess.restype = ctypes.c_long


def _last_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


class JobObject:
    def __init__(self, *, kill_on_close: bool):
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise _last_error()
        self._handle = handle
        self._kill_on_close = kill_on_close
        self._lock = threading.Lock()
        self._preserved = False
        self._terminated = False
        self._closed = False
        self._set_limit_flags(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE if kill_on_close else 0)

    def _set_limit_flags(self, flags: int) -> None:
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = flags
        ok = _kernel32.SetInformationJobObject(
            self._handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            raise _last_error()

    @property
    def terminated(self) -> bool:
        return self._terminated

    @property
    def preserved(self) -> bool:
        return self._preserved

    def spawn_contained(self, args, **popen_kwargs) -> subprocess.Popen:
        flags = popen_kwargs.pop("creationflags", 0) | CREATE_SUSPENDED
        proc = subprocess.Popen(args, creationflags=flags, **popen_kwargs)
        process = _kernel32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME,
            False,
            proc.pid,
        )
        if not process:
            err = _last_error()
            proc.kill()
            proc.wait()
            raise err
        try:
            if not _kernel32.AssignProcessToJobObject(self._handle, process):
                err = _last_error()
                proc.kill()
                proc.wait()
                raise err
            status = _ntdll.NtResumeProcess(process)
            if status < 0:
                proc.kill()
                proc.wait()
                raise OSError(f"NtResumeProcess failed: NTSTATUS {status & 0xFFFFFFFF:#010x}")
        finally:
            _kernel32.CloseHandle(process)
        return proc

    def contain_pid(self, pid: int) -> None:
        process = _kernel32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME,
            False,
            pid,
        )
        if not process:
            raise _last_error()
        try:
            if not _kernel32.AssignProcessToJobObject(self._handle, process):
                raise _last_error()
            status = _ntdll.NtResumeProcess(process)
            if status < 0:
                raise OSError(f"NtResumeProcess failed: NTSTATUS {status & 0xFFFFFFFF:#010x}")
        finally:
            _kernel32.CloseHandle(process)

    def terminate(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("JobObject already closed")
            if self._preserved or self._terminated:
                return
            if not _kernel32.TerminateJobObject(self._handle, 1):
                raise _last_error()
            self._terminated = True

    def preserve_descendants(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("JobObject already closed")
            if self._preserved or self._terminated:
                return
            if self._kill_on_close:
                self._set_limit_flags(0)
            self._preserved = True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if not _kernel32.CloseHandle(self._handle):
                raise _last_error()
