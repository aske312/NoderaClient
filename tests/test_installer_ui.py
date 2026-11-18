import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from nodera.install_service import AntivirusProtectionError, Plan
from nodera.installer_ui import INSTALL_STYLE, InstallerWindow, OptionsDialog
from nodera.theme import STYLE


class PreviewService:
    def inspect(self, folder):
        return Plan("install", folder, None), True, False

    def run(self, *args, **kwargs):
        raise AssertionError("A preview must not call the installation service")


def wait(app, window):
    deadline = time.monotonic() + 5
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    app.processEvents()
    assert window.worker is None


def test_preview_finishes_without_entering_installation_engine(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE + INSTALL_STYLE)
    window = InstallerWindow(
        PreviewService(),
        {"version": "0.0.3", "revision": "abc123"},
        "MIT",
        tmp_path / "product",
        preview=True,
    )
    window.show()
    app.processEvents()
    wait(app, window)
    assert window.primary.isEnabled()
    window.activate()
    assert window.finished and window.primary.text() == "Готово"
    assert not (tmp_path / "product").exists()
    window.close()
    app.processEvents()
    assert not window.isVisible()


def test_folder_settings_require_confirmation_and_validate_destination(tmp_path):
    app = QApplication.instance() or QApplication([])
    parent = InstallerWindow(
        PreviewService(), {"version": "0.0.3"}, "MIT", tmp_path / "product", preview=True
    )
    dialog = OptionsDialog(tmp_path / "product", True, False, False, parent)
    dialog.folder.setText("relative-folder")
    dialog.accept()
    assert dialog.result() == 0 and dialog.error.text()
    dialog.folder.setText(str(tmp_path / "Nodera Client"))
    dialog.accept()
    assert dialog.result() == dialog.DialogCode.Accepted
    dialog.close()
    parent.close()
    app.processEvents()


def test_installer_offers_windows_security_after_antivirus_denial(tmp_path, monkeypatch):
    class BlockedService(PreviewService):
        def run(self, *args, **kwargs):
            raise AntivirusProtectionError("Windows заблокировала файл.")

    app = QApplication.instance()
    opened = []
    monkeypatch.setattr("nodera.installer_ui.QMessageBox.warning", lambda *args: None)
    monkeypatch.setattr(
        "nodera.installer_ui.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    window = InstallerWindow(BlockedService(), {"version": "0.0.3"}, "MIT", tmp_path / "product")
    window.show()
    app.processEvents()
    wait(app, window)
    assert window.security.isHidden()
    window.activate()
    wait(app, window)
    assert window.security.isVisible()
    assert not window.finished and window.primary.text() == "Повторить"

    def open_security_settings():
        dialog = app.activeModalWidget()
        if dialog:
            try:
                buttons = dialog.findChild(QDialogButtonBox)
                next(
                    button
                    for button in buttons.buttons()
                    if button.text() == "Открыть настройки Windows"
                ).click()
            finally:
                dialog.reject()

    QTimer.singleShot(0, open_security_settings)
    window.security.click()
    assert opened == ["ms-settings:windowsdefender"]
    window.close()


def test_installer_offers_repair_if_executable_disappears_before_launch(tmp_path, monkeypatch):
    app = QApplication.instance()
    window = InstallerWindow(PreviewService(), {"version": "0.0.3"}, "MIT", tmp_path / "product")
    window.show()
    app.processEvents()
    wait(app, window)
    window.completed({"folder": window.folder, "reconnect": False, "warning": ""})

    def missing(*args, **kwargs):
        raise FileNotFoundError("Nodera.exe")

    monkeypatch.setattr("nodera.installer_ui.subprocess.Popen", missing)
    window.activate()
    assert window.isVisible() and window.security.isVisible() and window.repair.isVisible()
    window.close()
