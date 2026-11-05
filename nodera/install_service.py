"""Installer operations; UI and preview never bypass this boundary."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from nodera.models import ClientError

APP_ID = "{6201CE8F-E587-47BC-BE30-024E3E63C508}_is1"
TRANSIENT = {"runtime", "updates", "logs", "nodera.lock", "maintenance.lock"}


@dataclass(frozen=True)
class Installed:
    folder: Path
    version: str
    revision: str
    uninstaller: Path | None = None
    desktop: bool = False


@dataclass(frozen=True)
class Plan:
    action: str
    folder: Path
    installed: Installed | None


def read_installed(folder, *, registry_version="", desktop=False):
    folder = Path(folder)
    try:
        info = json.loads((folder / "_internal/nodera/resources/build-info.json").read_text())
    except (OSError, ValueError, TypeError):
        info = {}
    version = info.get("version") or registry_version
    revision = info.get("revision") or info.get("build") or ""
    uninstallers = sorted(folder.glob("unins[0-9][0-9][0-9].exe")) if folder.is_dir() else []
    return Installed(
        folder, str(version), str(revision), uninstallers[0] if uninstallers else None, desktop
    )


def find_installed():
    import winreg

    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
    # Nodera's published installers are administrative, registered in HKLM.
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | view
            ) as handle:
                folder = winreg.QueryValueEx(handle, "InstallLocation")[0]
                try:
                    version = winreg.QueryValueEx(handle, "DisplayVersion")[0]
                except OSError:
                    version = ""
                try:
                    tasks = winreg.QueryValueEx(handle, "Inno Setup: Selected Tasks")[0]
                except OSError:
                    tasks = ""
            if folder and Path(folder).is_absolute():
                return read_installed(
                    folder, registry_version=version, desktop="desktopicon" in tasks.split(",")
                )
        except OSError:
            continue
    return None


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def intact(folder, manifest):
    for relative, expected in manifest["files"].items():
        path = Path(folder) / relative
        try:
            if path.stat().st_size != expected["size"] or file_digest(path) != expected["sha256"]:
                return False
        except OSError:
            return False
    return True


def choose_plan(installed, identity, manifest, default_folder, *, force_repair=False):
    if installed is None:
        return Plan("install", Path(default_folder), None)
    revision = str(identity.get("revision") or identity.get("build") or "")
    same = (
        installed.version == identity["version"]
        and bool(revision)
        and bool(installed.revision)
        and installed.revision == revision
    )
    if not same:
        return Plan("replace", installed.folder, installed)
    damaged = installed.uninstaller is None or not installed.uninstaller.is_file()
    return Plan(
        "repair"
        if force_repair or damaged or not intact(installed.folder, manifest)
        else "current",
        installed.folder,
        installed,
    )


def snapshot_data(root, backup_parent):
    root, backup_parent = Path(root), Path(backup_parent)
    if not root.exists():
        return None
    backup = backup_parent / (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]
    )
    shutil.copytree(root, backup, symlinks=True, ignore=shutil.ignore_patterns(*TRANSIENT))
    return backup


def restore_data(backup, root):
    if backup:
        shutil.copytree(backup, root, dirs_exist_ok=True, symlinks=True)


def stop_client(root, folder, progress):
    from PySide6.QtCore import QLockFile

    from nodera.install_windows import stop_owned_processes
    from nodera.instance import instance_name, request_restart

    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / "nodera.lock"))
    reconnect = False
    if not lock.tryLock(100):
        progress("Завершаем работу Nodera…")
        try:
            reconnect = request_restart(instance_name(root)) is True
        except ClientError:
            pass
        deadline = time.monotonic() + 45
        while not lock.tryLock(100) and time.monotonic() < deadline:
            time.sleep(0.05)
        if not lock.isLocked():
            stop_owned_processes(Path(folder) / "Nodera.exe")
            if not lock.tryLock(3000):
                raise ClientError(
                    "Nodera ещё завершает работу. Дождитесь остановки подключения и повторите попытку."
                )
    # Covers a process starting before it acquired its per-user lock.
    try:
        stop_owned_processes(Path(folder) / "Nodera.exe")
        from nodera.system_proxy import SystemProxy

        SystemProxy(root).restore()
    except Exception:
        lock.unlock()
        raise
    return lock, reconnect


def explain_failure(code, log, *, windows=False):
    actions = {
        2: "Не найден необходимый файл. Скачайте Nodera-Setup.exe повторно и запустите его из доступной папки.",
        5: "Проверьте разрешения папки для вашей учётной записи администратора.",
        112: "Освободите место на выбранном диске и повторите попытку.",
        1223: "Разрешите изменения в запросе Windows и повторите попытку.",
    }
    if windows:
        action = actions.get(
            code,
            "Проверьте доступность выбранной папки и диска. Сохраните журнал, если ошибка повторяется.",
        )
    elif code in (2, 5):
        action = "Операция отменена. Чтобы продолжить, запустите Nodera-Setup.exe ещё раз."
    elif code in (7, 8):
        action = "Перезагрузите Windows, затем повторите запуск установщика."
    else:
        action = "Не удалось завершить запись файлов. Причина указана ниже; сохраните журнал для диагностики."
    return f"{action}\nКод: {code}. Журнал: {log}"


class InstallService:
    def __init__(self, identity, manifest, engine, root, *, preview=False):
        self.identity, self.manifest = identity, manifest
        self.engine, self.root = Path(engine), Path(root)
        self.preview = preview

    def inspect(self, folder, *, force_repair=False):
        installed = find_installed()
        if installed is None and (Path(folder) / "Nodera.exe").is_file():
            installed = read_installed(folder)
        plan = choose_plan(
            installed, self.identity, self.manifest, folder, force_repair=force_repair
        )
        from nodera.autostart import is_enabled

        try:
            startup = is_enabled() if installed else True
        except ClientError:
            startup = True
        return plan, startup, installed.desktop if installed else False

    def _execute(self, arguments, log):
        try:
            result = subprocess.run(
                arguments,
                capture_output=True,
                timeout=240,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClientError(
                f"Операция не завершилась вовремя. Не удаляйте резервную копию; повторите запуск установщика.\nЖурнал: {log}"
            ) from exc
        except OSError as exc:
            raise ClientError(
                explain_failure(getattr(exc, "winerror", None) or exc.errno, log, windows=True)
            ) from exc
        if result.returncode not in (0, 3010):
            detail = ""
            try:
                lines = Path(log).read_text(encoding="utf-8-sig", errors="replace").splitlines()
                detail = "\n" + "\n".join(lines[-12:])
            except OSError:
                pass
            windows_error = re.search(r"(?:Ошибка|Error)\s+(\d+)\s*:", detail, re.IGNORECASE)
            if windows_error:
                raise ClientError(
                    explain_failure(int(windows_error.group(1)), log, windows=True) + detail
                )
            raise ClientError(explain_failure(result.returncode, log) + detail)

    def _engine(self, folder, desktop, log):
        self._execute(
            [
                str(self.engine),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/SP-",
                "/NORESTART",
                "/ACCEPTLICENSE=1",
                f"/DIR={folder}",
                f"/LOG={log}",
                "/TASKS=" + ("desktopicon" if desktop else ""),
            ],
            log,
        )

    def run(self, plan, startup, desktop, progress, *, force_repair=False):
        if self.preview:
            raise ClientError(
                "Предпросмотр не устанавливает приложение. Откройте Nodera-Setup.exe."
            )
        import ctypes

        if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
            raise ClientError(
                "Запустите Nodera-Setup.exe от имени администратора и разрешите изменения в запросе Windows."
            )
        if not self.engine.is_file() or file_digest(self.engine) != self.manifest["engine_sha256"]:
            raise ClientError("Файл установщика повреждён. Скачайте Nodera-Setup.exe повторно.")
        # Detect a changed installation again immediately before touching it.
        actual = choose_plan(
            find_installed(), self.identity, self.manifest, plan.folder, force_repair=force_repair
        )
        if actual.action == "current":
            return {"folder": actual.folder, "reconnect": False, "warning": ""}
        folder = actual.folder
        folder_parent = folder
        while not folder_parent.exists():
            if folder_parent.parent == folder_parent:
                raise ClientError(
                    "Выбранный диск недоступен. Подключите его или выберите другую папку."
                )
            folder_parent = folder_parent.parent
        needed = sum(item["size"] for item in self.manifest["files"].values()) * 2
        if shutil.disk_usage(folder_parent).free < needed:
            raise ClientError(
                "На выбранном диске недостаточно свободного места. Освободите место и повторите попытку."
            )
        lock, reconnect = stop_client(self.root, folder, progress)
        backup = None
        logs = self.root.parent / "NoderaClient-install-logs"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        try:
            logs.mkdir(parents=True, exist_ok=True)
            progress("Сохраняем ваши данные…")
            backup = snapshot_data(self.root, self.root.parent / "NoderaClient-install-backups")
            if actual.action == "replace":
                installed = actual.installed
                uninstaller = installed.uninstaller
                if uninstaller is None:
                    progress("Восстанавливаем компонент удаления…")
                    self._engine(folder, desktop, logs / f"{stamp}-recover.log")
                    uninstaller = read_installed(folder).uninstaller
                if uninstaller is None:
                    raise ClientError(
                        "Не удалось восстановить компонент удаления. Ваши данные сохранены; повторите запуск установщика."
                    )
                progress("Подготавливаем новую версию…")
                uninstall_log = logs / f"{stamp}-remove.log"
                self._execute(
                    [
                        str(uninstaller),
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART",
                        f"/LOG={uninstall_log}",
                    ],
                    uninstall_log,
                )
            progress("Подготавливаем Nodera…")
            self._engine(folder, desktop, logs / f"{stamp}-install.log")
            restore_data(backup, self.root)
            warning = ""
            progress("Завершаем подготовку…")
            try:
                exe = folder / "Nodera.exe"
                self._execute(
                    [str(exe), "--configure-autostart", "enable" if startup else "disable"],
                    logs / f"{stamp}-startup.log",
                )
            except ClientError:
                warning = "Приложение готово, но автозапуск не настроен. Откройте «Настройки → Основное → Запускать с Windows»."
            return {"folder": folder, "reconnect": reconnect, "warning": warning}
        except Exception as exc:
            restore_data(backup, self.root)
            if isinstance(exc, ClientError):
                raise ClientError(f"{exc}\nВаши данные сохранены: {backup or self.root}") from exc
            raise ClientError(
                f"Не удалось завершить подготовку: {exc}.\nДанные сохранены: {backup or self.root}"
            ) from exc
        finally:
            lock.unlock()
