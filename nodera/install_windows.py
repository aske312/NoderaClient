"""Stop only the exact installed Nodera executable after graceful shutdown."""

import ctypes
import os
import time
from ctypes import wintypes
from pathlib import Path

from nodera.models import ClientError


def matching_processes(executable):
    if os.name != "nt":
        return []
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class Entry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD),
            ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
            ("name", wintypes.WCHAR * 260),
        ]

    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ClientError("Не удалось проверить сеанс Windows. Повторите запуск установщика.")
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ClientError(
            "Не удалось проверить работающий клиент. Повторите запуск установщика от имени администратора."
        )
    result = []
    target = str(Path(executable).resolve()).casefold()
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            process_session = wintypes.DWORD()
            same_session = (
                kernel.ProcessIdToSessionId(entry.pid, ctypes.byref(process_session))
                and process_session.value == session.value
            )
            if same_session and entry.pid != os.getpid() and entry.name.casefold() == "nodera.exe":
                handle = kernel.OpenProcess(0x1000, False, entry.pid)
                if handle:
                    try:
                        size = wintypes.DWORD(32768)
                        value = ctypes.create_unicode_buffer(size.value)
                        if kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size)):
                            if str(Path(value.value).resolve()).casefold() == target:
                                result.append(entry.pid)
                    finally:
                        kernel.CloseHandle(handle)
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


def stop_owned_processes(executable):
    pids = matching_processes(executable)
    if not pids:
        return
    deadline = time.monotonic() + 3
    while pids and time.monotonic() < deadline:
        time.sleep(0.1)
        pids = matching_processes(executable)
    if not pids:
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    for pid in pids:
        handle = kernel.OpenProcess(0x00101001, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                continue
            raise ClientError(
                "Windows не разрешила завершить Nodera. Повторите запуск установщика от имени администратора."
            )
        try:
            if kernel.WaitForSingleObject(handle, 0) == 0:
                continue
            process_session = wintypes.DWORD()
            current_session = wintypes.DWORD()
            if not kernel.ProcessIdToSessionId(
                pid, ctypes.byref(process_session)
            ) or not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(current_session)):
                raise ClientError(
                    "Не удалось проверить сеанс процесса Nodera. Повторите запуск установщика."
                )
            if process_session.value != current_session.value:
                continue
            value = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(value))
            if not kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size)):
                raise ClientError(
                    "Не удалось проверить процесс Nodera. Повторите запуск от имени администратора."
                )
            if (
                str(Path(value.value).resolve()).casefold()
                != str(Path(executable).resolve()).casefold()
            ):
                continue
            if (
                not kernel.TerminateProcess(handle, 0)
                and kernel.WaitForSingleObject(handle, 0) != 0
            ):
                raise ClientError(
                    "Не удалось завершить Nodera. Дождитесь остановки клиента и повторите попытку."
                )
            if kernel.WaitForSingleObject(handle, 10000) != 0:
                raise ClientError(
                    "Windows ещё не завершила Nodera. Дождитесь остановки клиента и повторите попытку."
                )
        finally:
            kernel.CloseHandle(handle)
