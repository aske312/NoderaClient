import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from nodera.models import Settings
from nodera.storage import Store
from nodera.ui import MainWindow


def test_close_keeps_client_running_in_tray_and_explicit_exit_stops_it(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    monkeypatch.setattr(window.tray, "isVisible", lambda: True)
    completed = []
    window.shutdown_complete.connect(lambda: completed.append(True))
    try:
        window.show()
        app.processEvents()
        window.close()
        app.processEvents()
        assert not window.isVisible()
        assert not window.closing and not window.shutdown_event.is_set()
        assert window.timer.isActive() and not completed
        window.reveal()
        app.processEvents()
        assert window.isVisible()
        window.exit_app()
        app.processEvents()
        assert window.closing and window.shutdown_event.is_set()
        assert completed == [True]
        assert not window.timer.isActive()
    finally:
        window.exit_app()
        app.processEvents()
