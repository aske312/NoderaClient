"""Use Windows' standard UAC flow; never modify UAC or consent policies."""

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from nodera.core import is_admin
from nodera.models import ClientError


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("mask", wintypes.ULONG),
        ("window", wintypes.HWND),
        ("verb", wintypes.LPCWSTR),
        ("file", wintypes.LPCWSTR),
        ("parameters", wintypes.LPCWSTR),
        ("directory", wintypes.LPCWSTR),
        ("show", ctypes.c_int),
        ("instance", wintypes.HINSTANCE),
        ("id_list", ctypes.c_void_p),
        ("class_name", wintypes.LPCWSTR),
        ("class_key", wintypes.HKEY),
        ("hot_key", wintypes.DWORD),
        ("icon", wintypes.HANDLE),
        ("process", wintypes.HANDLE),
    ]


def elevation_command(wait_for_pid=None):
    executable = Path(sys.executable).resolve()
    arguments = []
    skip = False
    for value in sys.argv[1:]:
        if skip:
            skip = False
        elif value == "--wait-for-exit":
            skip = True
        elif value != "--connect" or wait_for_pid is None:
            arguments.append(value)
    if wait_for_pid is not None:
        arguments.extend(["--wait-for-exit", str(wait_for_pid), "--connect"])
    if getattr(sys, "frozen", False):
        return executable, arguments, executable.parent
    script = Path(__file__).resolve().parents[1] / "main.py"
    windowed = executable.with_name("pythonw.exe")
    return (
        windowed if windowed.exists() else executable,
        [str(script), *arguments],
        script.parent,
    )


def launch_admin(wait_for_pid=None):
    executable, arguments, directory = elevation_command(wait_for_pid)
    info = ShellExecuteInfo()
    info.size = ctypes.sizeof(info)
    info.mask = 0x40 | 0x100 | 0x400  # process handle, synchronous shell setup, no error UI
    info.verb, info.file = "runas", str(executable)
    info.parameters = subprocess.list2cmdline(arguments)
    info.directory, info.show = str(directory), 1
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    # ShellExecuteEx can use COM shell extensions, even for an executable.
    ole = ctypes.OleDLL("ole32")
    initialized = False
    try:
        try:
            ole.CoInitializeEx(None, 2)
            initialized = True
        except OSError:
            pass  # The calling thread can already have another COM apartment.
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            if ctypes.get_last_error() == 1223:
                return False
            raise ClientError("Windows не разрешила запуск Nodera с правами администратора.")
        if info.process:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(info.process)
        return True
    finally:
        if initialized:
            ole.CoUninitialize()


def ensure_admin():
    """Return True only when this process may enter the application."""
    if os.name != "nt" or is_admin():
        return True
    launch_admin()
    return False


def wait_for_previous_instance():
    """The elevated replacement must wait until the original releases its lock."""
    if "--wait-for-exit" not in sys.argv:
        return
    try:
        pid = int(sys.argv[sys.argv.index("--wait-for-exit") + 1])
        if not 0 < pid <= 0xFFFFFFFF or pid == os.getpid():
            raise ValueError
    except (ValueError, IndexError) as exc:
        raise ClientError("Некорректный запрос перезапуска Nodera.") from exc
    if os.name != "nt" or not is_admin():
        raise ClientError("Перезапуск TUN требует подтверждения Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        if ctypes.get_last_error() == 87:  # The original has already exited.
            return
        raise ClientError("Не удалось дождаться завершения предыдущего экземпляра Nodera.")
    try:
        if kernel.WaitForSingleObject(handle, 30000) != 0:
            raise ClientError("Предыдущий экземпляр Nodera ещё завершается. Повторите запуск.")
    finally:
        kernel.CloseHandle(handle)
