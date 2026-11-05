"""Per-user elevated logon task shared by the installer and application."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from nodera.models import ClientError

TASK_PREFIX = "Nodera-"


def startup_command():
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return executable, ["--autostart"], executable.parent
    windowed = executable.with_name("pythonw.exe")
    script = Path(__file__).resolve().parents[1] / "main.py"
    return (
        windowed if windowed.exists() else executable,
        [str(script), "--autostart"],
        script.parent,
    )


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def task_script(operation):
    executable, arguments, directory = startup_command()
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value\n"
        f"$taskName={ps_literal(TASK_PREFIX)}+$sid\n"
        "$task=Get-ScheduledTask -TaskName $taskName -TaskPath '\\' -ErrorAction SilentlyContinue\n"
    )
    if operation == "query":
        return script + "[bool]($task -and $task.Settings.Enabled) | ConvertTo-Json -Compress\n"
    if operation == "disable":
        return script + (
            "if($task){Unregister-ScheduledTask -TaskName $taskName -TaskPath '\\' -Confirm:$false}\n"
            "'false'\n"
        )
    if operation == "remove":
        return script + (
            f"$executable={ps_literal(executable)}\n"
            f"Get-ScheduledTask -TaskPath '\\' | Where-Object {{$_.TaskName -like {ps_literal(TASK_PREFIX + '*')} "
            "-and @($_.Actions | Where-Object Execute -eq $executable).Count -gt 0} | "
            "Unregister-ScheduledTask -Confirm:$false\n'false'\n"
        )
    if operation != "enable":
        raise ValueError("Unknown startup task operation")
    if not executable.is_file():
        raise ClientError("Не найден файл приложения для автозапуска.")
    return script + (
        f"$action=New-ScheduledTaskAction -Execute {ps_literal(executable)} "
        f"-Argument {ps_literal(subprocess.list2cmdline(arguments))} "
        f"-WorkingDirectory {ps_literal(directory)}\n"
        "$trigger=New-ScheduledTaskTrigger -AtLogOn -User $sid\n"
        "$principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Highest\n"
        "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew "
        "-ExecutionTimeLimit ([TimeSpan]::Zero)\n"
        "Register-ScheduledTask -TaskName $taskName -TaskPath '\\' -Action $action "
        "-Trigger $trigger -Principal $principal -Settings $settings "
        "-Description 'Nodera logon startup' -Force | Out-Null\n"
        "'true'\n"
    )


def run_operation(operation):
    if os.name != "nt":
        raise ClientError("Автозапуск доступен только в Windows.")
    script = task_script(operation)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    executable = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise ValueError("Task Scheduler operation failed")
        value = json.loads(result.stdout.strip())
        if type(value) is not bool:
            raise ValueError("Invalid task state")
        return value
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ClientError(
            "Не удалось изменить или проверить автозапуск. Проверьте Планировщик заданий Windows."
        ) from exc


def is_enabled():
    return run_operation("query")


def set_enabled(enabled):
    result = run_operation("enable" if enabled else "disable")
    if run_operation("query") != enabled:
        raise ClientError("Windows не подтвердила изменение автозапуска Nodera.")
    return result


def handle_command(arguments):
    """Installer commands run without constructing QApplication or touching profiles."""
    try:
        if arguments[:1] == ["--autostart-status"]:
            if len(arguments) != 1:
                return 2
            return 0 if is_enabled() else 1
        if arguments[:1] == ["--remove-autostart"]:
            if len(arguments) != 1:
                return 2
            run_operation("remove")
            return 0
        if arguments[:1] == ["--configure-autostart"]:
            if len(arguments) != 2 or arguments[1] not in {"enable", "disable"}:
                return 2
            set_enabled(arguments[1] == "enable")
            return 0
    except ClientError:
        return 2
    return None
