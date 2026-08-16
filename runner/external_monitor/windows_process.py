from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
STILL_ACTIVE = 259
TH32CS_SNAPTHREAD = 0x00000004
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class FILETIME(ctypes.Structure):
    _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = (
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    )


class IO_COUNTERS(ctypes.Structure):
    _fields_ = (
        ("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong),
    )


class THREADENTRY32(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    )


class WindowsProcessSampler:
    def __init__(self, pid: int) -> None:
        if os.name != "nt":
            raise OSError("Windows process telemetry is available only on Windows")
        self.pid = pid
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        self.kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
        self.kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
        self.kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME))
        self.kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        self.kernel32.GetProcessIoCounters.argtypes = (wintypes.HANDLE, ctypes.POINTER(IO_COUNTERS))
        self.psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD)
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.handle:
            self.handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._sample_index = 0
        self._cached_thread_count: int | None = None

    def close(self) -> None:
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None

    def sample(self) -> dict[str, Any]:
        if self._cached_thread_count is None or self._sample_index % 10 == 0:
            self._cached_thread_count = self._thread_count()
        self._sample_index += 1
        return {
            "cpu_time_seconds": self._cpu_time_seconds(), **self._memory(),
            "thread_count": self._cached_thread_count, **self._io(),
            "process_alive": self._alive(), **self._window_state(),
        }

    def _cpu_time_seconds(self) -> float | None:
        created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not self.kernel32.GetProcessTimes(self.handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return (_filetime_value(kernel) + _filetime_value(user)) / 10_000_000.0

    def _memory(self) -> dict[str, int | None]:
        value = PROCESS_MEMORY_COUNTERS_EX(); value.cb = ctypes.sizeof(value)
        if not self.psapi.GetProcessMemoryInfo(self.handle, ctypes.byref(value), value.cb):
            return {"working_set_bytes": None, "private_bytes": None, "page_fault_count": None}
        return {"working_set_bytes": int(value.WorkingSetSize), "private_bytes": int(value.PrivateUsage), "page_fault_count": int(value.PageFaultCount)}

    def _io(self) -> dict[str, int | None]:
        value = IO_COUNTERS()
        if not self.kernel32.GetProcessIoCounters(self.handle, ctypes.byref(value)):
            return {"io_read_bytes": None, "io_write_bytes": None}
        return {"io_read_bytes": int(value.ReadTransferCount), "io_write_bytes": int(value.WriteTransferCount)}

    def _alive(self) -> bool | None:
        exit_code = wintypes.DWORD()
        if not self.kernel32.GetExitCodeProcess(self.handle, ctypes.byref(exit_code)):
            return None
        return exit_code.value == STILL_ACTIVE

    def _thread_count(self) -> int | None:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return None
        count, entry = 0, THREADENTRY32(); entry.dwSize = ctypes.sizeof(entry)
        try:
            success = self.kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while success:
                if entry.th32OwnerProcessID == self.pid:
                    count += 1
                success = self.kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            return count
        finally:
            self.kernel32.CloseHandle(snapshot)

    def _window_state(self) -> dict[str, bool | None]:
        windows: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(hwnd: int, _: int) -> bool:
            owner = wintypes.DWORD(); self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == self.pid and self.user32.IsWindowVisible(hwnd):
                windows.append(hwnd)
            return True

        if not self.user32.EnumWindows(collect, 0):
            return {"is_foreground": None, "is_minimized": None}
        if not windows:
            return {"is_foreground": False, "is_minimized": None}
        foreground = self.user32.GetForegroundWindow()
        return {"is_foreground": foreground in windows, "is_minimized": any(bool(self.user32.IsIconic(hwnd)) for hwnd in windows)}


def _filetime_value(value: FILETIME) -> int:
    return (int(value.high) << 32) | int(value.low)
