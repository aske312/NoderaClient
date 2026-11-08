import ctypes
import json
import os
import sys
from urllib.parse import urlsplit

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from nodera.autostart import handle_command
from nodera.core import secure_directory
from nodera.elevation import ensure_admin, wait_for_previous_instance
from nodera.instance import RestartServer, acquire_instance, instance_name
from nodera.models import ClientError, ProfileLibrary, Settings
from nodera.storage import Store, data_directory
from nodera.system_proxy import SystemProxy
from nodera.ui import STYLE, MainWindow, app_icon
from nodera.versioning import build_info


def development_subscription_proxy(arguments):
    value = next(
        (arg.partition("=")[2] for arg in arguments if arg.startswith("--subscription-proxy=")),
        None,
    )
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.port is not None
            and parsed.port > 0
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ClientError("Прокси стенда должен быть локальным HTTP-адресом с портом.")
    return value


def set_app_identity(*, testing=False):
    if os.name == "nt":
        try:
            function = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
            function.argtypes = [ctypes.c_wchar_p]
            function.restype = ctypes.c_long
            function("NoderaClient.Nodera.Test" if testing else "NoderaClient.Nodera")
        except OSError:
            pass


def main() -> int:
    command_result = handle_command(sys.argv[1:])
    if command_result is not None:
        return command_result
    development = "--dev" in sys.argv and not getattr(sys, "frozen", False)
    testing = build_info().get("channel") == "test"
    set_app_identity(testing=testing)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Nodera")
    app.setOrganizationName("NoderaClient")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    try:
        subscription_proxy = (
            development_subscription_proxy(sys.argv[1:]) if development or testing else None
        )
        if not development and not ensure_admin():
            return 0
        wait_for_previous_instance()
        root = data_directory(development=development, testing=testing)
        secure_directory(root)
        lock = QLockFile(str(root / "nodera.lock"))
        resume = acquire_instance(lock, instance_name(root))
        # No other instance can own these sessions after obtaining the per-user lock.
        SystemProxy(root).restore()
        import shutil

        for stale in (root / "runtime").glob("session-*"):
            if stale.is_dir():
                shutil.rmtree(stale)
        store = Store(root)
        if testing:
            from nodera.demo import prepare_demo

            prepare_demo(store)
        network_path = root / "test-network.json"
        if testing and subscription_proxy is None and network_path.exists():
            try:
                network = json.loads(network_path.read_text(encoding="utf-8-sig"))
                subscription_proxy = development_subscription_proxy(
                    ["--subscription-proxy=" + network["subscription_proxy"]]
                )
            except (ValueError, KeyError, TypeError) as exc:
                raise ClientError("Некорректные сетевые настройки тестовой сборки.") from exc
        if development and not (root / "settings.json").exists():
            store.save_settings(
                Settings(
                    tun=False,
                    system_proxy=False,
                    update_hours=0,
                    geo_auto=False,
                    client_location_attempted=True,
                    close_to_tray=False,
                )
            )
        settings = store.load_settings()
        warning = None
        try:
            library = store.load_library()
        except ClientError as exc:
            library = ProfileLibrary()
            warning = str(exc)
        if testing:
            from nodera.demo import DemoWindow

            window = DemoWindow(store, settings, library.active, library=library)
        else:
            window = MainWindow(store, settings, library.active, library=library)
        if testing:
            window.subscription_proxy = subscription_proxy
            window.setWindowTitle("Nodera Test · Демо" if window.demo_mode else "Nodera Test")
            window.tray.setToolTip("Nodera Test")
            window.app_update_timer.stop()
            window.app_update_check.setEnabled(False)
            window.app_update_status.setText(
                "Тестовая сборка · обновляется через локальную сборку приложения"
            )
        if development:
            window.subscription_proxy = subscription_proxy
            window.client_location_timer.stop()
            window.setWindowTitle("Nodera · тестовый стенд")
            window.tray.setToolTip("Nodera · тестовый стенд")
        # Closing a hidden tray window does not emit lastWindowClosed.
        window.shutdown_complete.connect(app.quit)
        handover = RestartServer(
            instance_name(root),
            lambda: window.connected or window.pending_connect,
            window.exit_app,
            app,
        )
        if "--autostart" not in sys.argv or not window.tray.isVisible():
            window.show()
        if warning:
            window.error(warning)
        elif library.active and (settings.auto_connect or "--connect" in sys.argv or resume):
            QTimer.singleShot(0, window.toggle)
        result = app.exec()
        window.core.stop("application exit")
        handover.server.close()
        lock.unlock()
        return result
    except (ClientError, OSError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ClientError)
            else "Не удалось открыть каталог данных Nodera. Проверьте права доступа."
        )
        QMessageBox.critical(None, "Nodera", message)
        return 1
