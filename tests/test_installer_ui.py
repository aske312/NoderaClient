import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.install_service import Plan
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
