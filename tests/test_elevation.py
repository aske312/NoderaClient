import ctypes
import os
from pathlib import Path

import pytest

from nodera import elevation


@pytest.mark.parametrize("admin,cancel", [(True, False), (False, False), (False, True)])
def test_startup_requires_admin_and_never_runs_after_cancel(monkeypatch, admin, cancel):
    calls = []
    monkeypatch.setattr(elevation, "is_admin", lambda: admin)
    monkeypatch.setattr(elevation, "launch_admin", lambda: calls.append(True) or not cancel)
    assert elevation.ensure_admin() is admin
    assert len(calls) == (0 if admin else 1)


def test_startup_elevation_preserves_connect_flag(monkeypatch):
    monkeypatch.setattr(elevation.sys, "argv", ["Nodera.exe", "--connect"])
    assert "--connect" in elevation.elevation_command()[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows startup")
def test_normal_startup_does_not_request_admin_or_wait(monkeypatch):
    monkeypatch.setattr(elevation.sys, "argv", ["Nodera.exe"])
    monkeypatch.setattr(elevation, "is_admin", lambda: pytest.fail("Startup requested admin"))
    elevation.wait_for_previous_instance()


def test_frozen_elevation_command_quotes_arguments_separately(monkeypatch):
    monkeypatch.setattr(elevation.sys, "frozen", True, raising=False)
    monkeypatch.setattr(elevation.sys, "executable", "C:/Program Files/Nodera/Nodera.exe")
    monkeypatch.setattr(elevation.sys, "argv", ["Nodera.exe", "argument with spaces"])
    executable, args, directory = elevation.elevation_command()
    assert executable == Path("C:/Program Files/Nodera/Nodera.exe").resolve()
    assert args == ["argument with spaces"]
    assert directory == executable.parent


def test_tun_relaunch_waits_for_current_instance_and_removes_old_handover_flags(monkeypatch):
    monkeypatch.setattr(
        elevation.sys, "argv", ["Nodera.exe", "--wait-for-exit", "100", "--connect"]
    )
    _, args, _ = elevation.elevation_command(wait_for_pid=200)
    assert args[-3:] == ["--wait-for-exit", "200", "--connect"]
    assert "100" not in args


@pytest.mark.parametrize(
    "arguments", [["--wait-for-exit"], ["--wait-for-exit", "bad"], ["--wait-for-exit", "0"]]
)
def test_invalid_handover_is_rejected(monkeypatch, arguments):
    monkeypatch.setattr(elevation.sys, "argv", ["Nodera.exe", *arguments])
    with pytest.raises(elevation.ClientError):
        elevation.wait_for_previous_instance()


@pytest.mark.skipif(os.name != "nt", reason="Windows process adapter")
@pytest.mark.parametrize("result", [0, 258])
def test_replacement_waits_for_original_and_always_closes_handle(monkeypatch, result):
    calls = []

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    class Kernel:
        OpenProcess = Function(lambda *args: 123)
        WaitForSingleObject = Function(
            lambda handle, timeout: calls.append((handle, timeout)) or result
        )
        CloseHandle = Function(lambda handle: calls.append(("close", handle)))

    monkeypatch.setattr(elevation.sys, "argv", ["Nodera.exe", "--wait-for-exit", "1"])
    monkeypatch.setattr(elevation, "is_admin", lambda: True)
    monkeypatch.setattr(elevation.ctypes, "WinDLL", lambda *args, **kwargs: Kernel())
    if result == 0:
        elevation.wait_for_previous_instance()
    else:
        with pytest.raises(elevation.ClientError):
            elevation.wait_for_previous_instance()
    assert calls == [(123, 30000), ("close", 123)]


@pytest.mark.skipif(os.name != "nt", reason="Windows shell adapter")
@pytest.mark.parametrize("cancel", [False, True])
def test_shell_uses_runas_and_handles_cancellation_without_real_uac(monkeypatch, cancel):
    calls = []

    class Function:
        def __call__(self, pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(elevation.ShellExecuteInfo)).contents
            assert info.verb == "runas"
            assert info.mask & 0x100
            assert info.show == 1
            calls.append(info.file)
            if cancel:
                ctypes.set_last_error(1223)
                return False
            return True

    class Shell:
        ShellExecuteExW = Function()

    monkeypatch.setattr(elevation.ctypes, "WinDLL", lambda *args, **kwargs: Shell())
    assert elevation.launch_admin() is (not cancel)
    assert len(calls) == 1
