import base64
import ctypes
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from nodera import autostart
from nodera.autostart import is_enabled as actual_enabled
from nodera.autostart import set_enabled as actual_set_enabled
from nodera.models import ClientError


def test_frozen_command_keeps_paths_separate_and_quotes_apostrophes(monkeypatch):
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", "C:/Program Files/Owner's Nodera/Nodera.exe")
    executable, arguments, directory = autostart.startup_command()
    assert arguments == ["--autostart"] and directory == executable.parent
    assert "Owner''s" in autostart.ps_literal(executable)


@pytest.mark.parametrize(
    "output,code,expected", [("true", 0, True), ("false", 0, False), ("{}", 0, None), ("", 1, None)]
)
def test_windows_task_adapter_validates_result(monkeypatch, output, code, expected):
    def run(command, **kwargs):
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        assert "Get-ScheduledTask" in script
        assert kwargs["timeout"] == 20 and kwargs["creationflags"] == subprocess_no_window()
        return SimpleNamespace(returncode=code, stdout=output)

    monkeypatch.setattr(autostart.subprocess, "run", run)
    if expected is None:
        with pytest.raises(ClientError):
            autostart.run_operation("query")
    else:
        assert autostart.run_operation("query") is expected


def subprocess_no_window():
    return autostart.subprocess.CREATE_NO_WINDOW


def test_task_uses_current_user_interactive_highest_and_no_password():
    script = autostart.task_script("enable")
    assert "-AtLogOn -User $sid" in script
    assert "-UserId $sid -LogonType Interactive -RunLevel Highest" in script
    assert "--autostart" in script and "-WorkingDirectory" in script
    assert "-Password" not in script and "SYSTEM" not in script


@pytest.mark.parametrize("enabled", [False, True])
def test_startup_changes_are_verified_with_windows(monkeypatch, enabled):
    operations = []

    def run(operation):
        operations.append(operation)
        return enabled

    monkeypatch.setattr(autostart, "run_operation", run)
    assert actual_set_enabled(enabled) is enabled
    assert operations == ["enable" if enabled else "disable", "query"]

    monkeypatch.setattr(autostart, "run_operation", lambda _: not enabled)
    with pytest.raises(ClientError, match="не подтвердила"):
        actual_set_enabled(enabled)


def test_installer_commands_report_errors_without_opening_application(monkeypatch):
    from nodera import app

    calls = []
    monkeypatch.setattr(autostart, "set_enabled", lambda value: calls.append(value))
    monkeypatch.setattr(app, "QApplication", lambda *_: pytest.fail("Installer command opened Qt"))
    monkeypatch.setattr(app.sys, "argv", ["Nodera.exe", "--configure-autostart", "enable"])
    assert app.main() == 0 and calls == [True]
    assert autostart.handle_command(["--configure-autostart", "invalid"]) == 2

    def fail(_):
        raise ClientError("Task Scheduler unavailable")

    monkeypatch.setattr(autostart, "set_enabled", fail)
    assert app.main() == 2


def test_installer_entry_point_does_not_load_qt():
    script = """
import runpy,sys
from pathlib import Path
import nodera.autostart as startup
startup.is_enabled=lambda: False
sys.modules['PySide6']=None
sys.argv=['main.py','--autostart-status']
runpy.run_path(str(Path(startup.__file__).parents[1]/'main.py'),run_name='__main__')
"""
    result = subprocess.run(
        [autostart.sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 1 and not result.stderr


@pytest.mark.skipif(
    os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin(),
    reason="Requires elevated Windows runner",
)
def test_real_logon_task_can_be_enabled_queried_and_removed(monkeypatch):
    # A separate task name avoids touching the user's Nodera task.
    monkeypatch.setattr(autostart, "TASK_PREFIX", "NoderaTest-" + uuid4().hex + "-")
    monkeypatch.setattr(
        autostart,
        "startup_command",
        lambda: (Path(autostart.sys.executable).resolve(), ["--autostart"], Path.cwd()),
    )
    try:
        assert actual_enabled() is False
        assert actual_set_enabled(True) is True
        assert actual_enabled() is True
        assert actual_set_enabled(False) is False
        assert actual_enabled() is False
    finally:
        actual_set_enabled(False)
