import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.controls import Switch
from nodera.models import Settings
from nodera.storage import Store, valid_client_point
from nodera.ui import MainWindow


def test_switches_and_advanced_settings_are_saved_and_applied(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.timer.stop()
    window.show()
    window.navigate(2)
    app.processEvents()
    assert isinstance(window.close_tray_check, Switch)
    assert window.close_tray_check.isChecked()
    QTest.mouseClick(window.close_tray_check, Qt.MouseButton.LeftButton, pos=QPoint(35, 12))
    assert not window.close_tray_check.isChecked()
    QTest.mouseClick(window.close_tray_check, Qt.MouseButton.LeftButton, pos=QPoint(35, 12))
    assert window.close_tray_check.isChecked()
    window.flow_poll_setting.setCurrentIndex(window.flow_poll_setting.findData(2))
    window.flow_history_setting.setCurrentIndex(window.flow_history_setting.findData(15))
    window.connection_log_size.setCurrentIndex(window.connection_log_size.findData(16))
    window.log_save_check.setChecked(False)
    window.client_country_setting.setCurrentIndex(window.client_country_setting.findData("DE"))
    window.save_settings()
    settings = Store(tmp_path).load_settings()
    assert settings.flow_poll_seconds == 2 and settings.flow_history_minutes == 15
    assert settings.client_point["code"] == "DE"
    assert settings.close_to_tray
    assert window.flow_monitor.retention_seconds == 900
    assert not window.core.trace.persist and window.core.connection_log.journal.persist
    assert window.core.connection_log.journal.max_bytes == 16 * 1024 * 1024
    window.settings_tabs.setCurrentIndex(3)
    assert window.save_button.isVisible()
    window.quit_requested = True
    window.close()
    app.processEvents()


def test_client_point_validation_rejects_bad_coordinates_and_paths():
    base = dict(code="DE", lat=50.1, lon=8.6, name="Frankfurt", source="manual")
    assert valid_client_point(base)
    assert not valid_client_point({**base, "lat": float("nan")})
    assert not valid_client_point({**base, "code": "//"})
    assert not valid_client_point({**base, "lon": 300})


def test_detected_point_populates_settings_without_changing_routes(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.timer.stop()
    point = dict(code="FI", lat=60.17, lon=24.94, name="Helsinki", source="ip-physical")
    monkeypatch.setattr("nodera.settings_ui.detect_client_location", lambda: point)
    window.detect_client_button.click()
    while window.inspections:
        app.processEvents()
        QTest.qWait(10)
    assert window.client_country_setting.currentData() == "FI"
    assert window.client_point_source == "ip-physical"
    assert not window.core.running
    window.save_settings()
    assert window.world_map.client_point["name"] == "Helsinki"
    window.close()
    app.processEvents()


def test_startup_switch_reads_real_state_and_reverts_failed_changes(tmp_path, monkeypatch):
    import time

    from nodera.models import ClientError

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("nodera.autostart.is_enabled", lambda: True)
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.timer.stop()

    def wait():
        deadline = time.monotonic() + 5
        while window.inspections and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(10)
        app.processEvents()
        assert not window.inspections

    try:
        window.navigate(2)
        wait()
        assert window.autostart_check.isChecked() and window.autostart_check.isEnabled()

        def fail(_):
            raise ClientError("Task Scheduler unavailable")

        monkeypatch.setattr("nodera.autostart.set_enabled", fail)
        window.autostart_check.setChecked(False)
        wait()
        assert window.autostart_check.isChecked()
        assert "Task Scheduler unavailable" in window.settings_result.text()
        monkeypatch.setattr("nodera.autostart.set_enabled", lambda value: value)
        window.autostart_check.setChecked(False)
        wait()
        assert not window.autostart_check.isChecked()
    finally:
        window.close()
        app.processEvents()
