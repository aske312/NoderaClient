import os
import threading
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.models import Profile, Settings
from nodera.storage import Store
from nodera.ui import MainWindow
from nodera.versioning import version_text
from tests.test_flow_monitor import flow


def make_window(tmp_path, profile=None):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0, geo_auto=False), profile)
    window.timer.stop()
    return app, window


def test_empty_dashboard_plus_menu_and_shared_version(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    window.show()
    app.processEvents()
    assert window.profile_add.isVisible()
    assert not window.profile_selector.isVisible()
    assert window.sidebar_toggle.y() > max(button.y() for button in window.nav.values())
    assert window.sidebar_toggle.width() == 28
    assert window.sidebar_version.text() == window.application_version.text() == version_text()
    assert version_text().startswith("v. 0.0.1 build:")
    from nodera.dialogs import SubscriptionDialog

    calls = []
    monkeypatch.setattr(SubscriptionDialog, "exec", lambda dialog: calls.append(True) or 0)
    window.profile_add.click()
    assert calls == [True]
    window.sidebar_toggle.click()
    # Finish the animation deterministically, even while a parallel build is busy.
    window.sidebar_animation.setCurrentTime(window.sidebar_animation.duration())
    app.processEvents()
    assert all(
        button.property("circular") and button.width() == button.height()
        for button in window.nav.values()
    )
    window.close()


def test_live_application_drilldown_sites_recent_and_search(tmp_path):
    app, window = make_window(tmp_path)
    window.poll = lambda: None
    window.show()
    window.connected = True
    window.flow_monitor.update(
        [flow(), flow("b", "other.example"), flow("c", "example.org", "service.exe")], now=100
    )
    window.navigate(8)
    window.timer.stop()
    app.processEvents()
    apps = window.flow_views[0]
    assert apps.topLevelItemCount() == 2
    chrome = next(
        apps.topLevelItem(index)
        for index in range(2)
        if apps.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)["name"] == "chrome.exe"
    )
    assert chrome.childCount() == 2
    QTest.mouseClick(
        apps.viewport(), Qt.MouseButton.LeftButton, pos=apps.visualItemRect(chrome).center()
    )
    assert chrome.isExpanded()
    window.render_flows(force=True)
    assert chrome.isExpanded()
    window.flow_search.setText("other.example")
    assert apps.topLevelItemCount() == 1
    assert chrome.childCount() == 1
    window.flow_search.clear()
    assert chrome.childCount() == 2
    assert chrome.isExpanded()
    window.flow_active_only.setChecked(True)
    window.flow_monitor.update([], now=101)
    window.render_flows(force=True)
    assert apps.topLevelItemCount() == 0
    window.flow_active_only.setChecked(False)
    assert apps.topLevelItemCount() == 2
    assert sum(apps.topLevelItem(index).childCount() for index in range(2)) == 3
    window.clear_recent_flows()
    assert apps.topLevelItemCount() == 0
    # No core was started by this synthetic metadata preview.
    window.connected = False
    window.close()
    app.processEvents()


def test_subscription_ring_reports_success_and_failure(tmp_path, monkeypatch):
    profile = Profile(
        "Test",
        "https://example.com/sub/test",
        {"proxies": [{"name": "A", "type": "ss"}]},
        datetime.now(timezone.utc).isoformat(),
    )
    app, window = make_window(tmp_path, profile)
    entered, release = threading.Event(), threading.Event()

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return profile

    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", fetch)
    window.load_subscription(profile.url, profile.name)
    assert entered.wait(2)
    assert window.power._subscription == "loading"
    assert "Обновление" in window.subscription_status.text()
    release.set()
    while window.task:
        app.processEvents()
        QTest.qWait(10)
    assert window.power._subscription == "success"
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    from nodera.models import ClientError

    def fail(*args, **kwargs):
        raise ClientError("Тестовая ошибка подписки")

    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", fail)
    window.load_subscription(profile.url, profile.name)
    while window.task:
        app.processEvents()
        QTest.qWait(10)
    assert window.power._subscription == "error"
    assert errors == ["Тестовая ошибка подписки"]
    window.show_subscription_state(None)
    assert not window.subscription_status.text()
    window.close()
    app.processEvents()
