import ctypes
import os
import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from nodera.autostart import handle_command
from nodera.core import secure_directory
from nodera.elevation import ensure_admin, wait_for_previous_instance
from nodera.instance import RestartServer, acquire_instance, instance_name
from nodera.models import ClientError, ProfileLibrary
from nodera.storage import Store, data_directory
from nodera.system_proxy import SystemProxy
from nodera.ui import STYLE, MainWindow, app_icon


def set_app_identity():
    if os.name == "nt":
        try:
            function = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
            function.argtypes = [ctypes.c_wchar_p]
            function.restype = ctypes.c_long
            function("NoderaClient.Nodera")
        except OSError:
            pass


def main() -> int:
    command_result = handle_command(sys.argv[1:])
    if command_result is not None:
        return command_result
    set_app_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("Nodera")
    app.setOrganizationName("NoderaClient")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    try:
        if not ensure_admin():
            return 0
        wait_for_previous_instance()
        root = data_directory()
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
        settings = store.load_settings()
        warning = None
        try:
            library = store.load_library()
        except ClientError as exc:
            library = ProfileLibrary()
            warning = str(exc)
        window = MainWindow(store, settings, library.active, library=library)
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
        elif "--connect" in sys.argv or resume:
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
