"""Transactional per-user WinINet proxy, with a separate crash recovery process.

API: https://learn.microsoft.com/windows/win32/wininet/setting-and-retrieving-internet-options
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

from nodera.models import ClientError
from nodera.storage import atomic_write, protect


class OptionValue(ctypes.Union):
    _fields_ = [("number", wintypes.DWORD), ("text", ctypes.c_void_p), ("time", wintypes.FILETIME)]


class Option(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("value", OptionValue)]


class OptionList(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("connection", wintypes.LPWSTR),
        ("count", wintypes.DWORD),
        ("error", wintypes.DWORD),
        ("options", ctypes.POINTER(Option)),
    ]


class WindowsProxy:
    def __init__(self):
        self.api = ctypes.WinDLL("wininet", use_last_error=True)
        self.api.InternetQueryOptionW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.api.InternetQueryOptionW.restype = wintypes.BOOL
        self.api.InternetSetOptionW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.api.InternetSetOptionW.restype = wintypes.BOOL

    def read(self):
        options = (Option * 4)()
        # FLAGS_UI preserves the user's auto-detect preference.
        for option, kind in zip(options, (10, 2, 3, 4), strict=True):
            option.kind = kind
        listing = OptionList(ctypes.sizeof(OptionList), None, 4, 0, options)
        size = wintypes.DWORD(ctypes.sizeof(listing))
        if not self.api.InternetQueryOptionW(None, 75, ctypes.byref(listing), ctypes.byref(size)):
            raise ClientError("Windows не разрешила прочитать настройки прокси.")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel.GlobalFree.restype = ctypes.c_void_p
        try:
            return {
                "flags": options[0].value.number,
                **{
                    key: ctypes.wstring_at(option.value.text) if option.value.text else ""
                    for key, option in zip(("server", "bypass", "pac"), options[1:], strict=True)
                },
            }
        finally:
            for option in options[1:]:
                if option.value.text:
                    kernel.GlobalFree(option.value.text)

    def write(self, values):
        options = (Option * 4)()
        options[0].kind = 1
        options[0].value.number = values["flags"]
        buffers = [ctypes.create_unicode_buffer(values[key]) for key in ("server", "bypass", "pac")]
        for option, kind, buffer in zip(options[1:], (2, 3, 4), buffers, strict=True):
            option.kind = kind
            option.value.text = ctypes.cast(buffer, ctypes.c_void_p).value
        listing = OptionList(ctypes.sizeof(OptionList), None, 4, 0, options)
        if not self.api.InternetSetOptionW(None, 75, ctypes.byref(listing), ctypes.sizeof(listing)):
            raise ClientError("Windows не разрешила изменить настройки прокси.")
        # Notify applications and refresh WinINet's cached configuration.
        for flag in (95, 37):
            self.api.InternetSetOptionW(None, flag, None, 0)


@contextmanager
def journal_lock(root):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    import hashlib

    name = hashlib.sha256(str(root.resolve()).lower().encode()).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, f"Local\\NoderaProxy-{name}")
    if not handle:
        raise ClientError("Не удалось защитить настройки прокси.")
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 10000) in (0, 0x80)
        if not acquired:
            raise ClientError("Настройки прокси заняты другим процессом Nodera.")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


class SystemProxy:
    def __init__(self, root: Path, backend=None, guard=None):
        self.root = root
        self.path = root / "proxy-session.dpapi"
        self.backend = backend
        self.guard = guard or self.start_guard

    def api(self):
        return self.backend if self.backend is not None else WindowsProxy()

    @property
    def pending(self):
        return self.path.exists()

    def read_journal(self):
        try:
            return json.loads(protect(self.path.read_bytes(), decrypt=True))
        except (ValueError, TypeError) as exc:
            raise ClientError("Не удалось прочитать резервную копию настроек прокси.") from exc

    def restore(self, token=None):
        if not self.pending:
            return
        with journal_lock(self.root):
            if not self.pending:
                return
            data = self.read_journal()
            if token and data["token"] != token:
                return
            api = self.api()
            current = api.read()
            original, applied = data["original"], data["applied"]
            # Preserve explicit changes made by the user or another application.
            # A partial application also rolls back fields still owned by Nodera.
            restored = dict(current)
            if current["server"] in (original["server"], applied["server"]):
                restored = {
                    key: original[key] if current[key] == applied[key] else current[key]
                    for key in original
                }
            if restored != current:
                api.write(restored)
            self.path.unlink()

    def enable(self, port):
        self.restore()
        with journal_lock(self.root):
            api = self.api()
            data = {
                "token": str(uuid4()),
                "original": api.read(),
                "applied": {
                    "flags": 3,
                    "server": f"127.0.0.1:{port}",
                    "bypass": "<local>;localhost;127.*;[::1]",
                    "pac": "",
                },
            }
            atomic_write(self.path, protect(json.dumps(data).encode()))
        # Start recovery before changing Windows; it survives termination of the UI.
        try:
            self.guard(data["token"])
            with journal_lock(self.root):
                api.write(data["applied"])
        except Exception:
            self.restore(data["token"])
            raise

    def start_guard(self, token):
        if getattr(sys, "frozen", False):
            command = [sys.executable]
        else:
            command = [sys.executable, "-m", "nodera.proxy_guard"]
        ready = self.root / f"proxy-ready-{token}"
        process = subprocess.Popen(
            [*command, "--proxy-guard", str(os.getpid()), str(self.root.resolve()), token],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise ClientError("Не удалось запустить восстановление системного прокси.")
                time.sleep(0.05)
        finally:
            ready.unlink(missing_ok=True)


def watch_parent(pid, root, token):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    parent = kernel.OpenProcess(0x100000, False, pid)
    proxy = SystemProxy(root)
    ready = root / f"proxy-ready-{token}"
    try:
        ready.touch()
        while parent and kernel.WaitForSingleObject(parent, 1000) == 0x102:
            with journal_lock(root):
                if not proxy.pending or proxy.read_journal()["token"] != token:
                    return
        proxy.restore(token)
    finally:
        ready.unlink(missing_ok=True)
        if parent:
            kernel.CloseHandle(parent)
