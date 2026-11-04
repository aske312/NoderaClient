import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.models import Profile, Settings
from nodera.storage import Store
from nodera.ui import MainWindow
from nodera.widgets import app_icon


def test_proxy_connects_without_requesting_tun_or_admin(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = Profile("P", "", {"proxies": [{"name": "A"}], "tun": {"enable": True}}, "")
    window = MainWindow(
        Store(tmp_path), Settings(tun=False, update_hours=0, geo_auto=False), profile
    )
    window.timer.stop()
    monkeypatch.setattr(
        "nodera.ui.is_admin", lambda: pytest.fail("Proxy checked administrator rights")
    )
    monkeypatch.setattr(
        "nodera.ui.launch_admin", lambda **kwargs: pytest.fail("Proxy requested UAC")
    )
    starts = []
    monkeypatch.setattr(
        window.core, "start", lambda profile, settings: starts.append(settings.tun) or "test"
    )
    monkeypatch.setattr(window.core, "check_connectivity", lambda: None)
    try:
        assert window.connection_mode.currentData() == "proxy"
        assert window.system_proxy_check.isEnabled()
        window.toggle()
        deadline = time.monotonic() + 3
        while (window.task or window.inspections) and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        assert starts == [False]
        assert window.connected and not window.session_tun
        assert window.network_state == "ok"
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("approved", [False, True])
def test_only_tun_requests_elevation_and_cancel_keeps_proxy_available(
    tmp_path, monkeypatch, approved
):
    app = QApplication.instance() or QApplication([])
    profile = Profile("P", "", {"proxies": [{"name": "A"}]}, "")
    store = Store(tmp_path)
    window = MainWindow(store, Settings(tun=True, update_hours=0, geo_auto=False), profile)
    window.timer.stop()
    requests, exits = [], []
    monkeypatch.setattr("nodera.ui.is_admin", lambda: False)

    def elevate(wait_for_pid):
        requests.append(wait_for_pid)
        return approved

    monkeypatch.setattr("nodera.ui.launch_admin", elevate)
    monkeypatch.setattr(window, "exit_app", lambda: exits.append(True))
    monkeypatch.setattr(
        window.core, "start", lambda *args: pytest.fail("TUN started before consent")
    )
    try:
        assert not window.system_proxy_check.isEnabled()
        window.toggle()
        assert requests == [os.getpid()]
        assert exits == ([True] if approved else [])
        assert window.task is None and not window.connected
        assert store.load_settings().tun is True
        if not approved:
            window.connection_mode.setCurrentIndex(window.connection_mode.findData("proxy"))
            assert window.system_proxy_check.isEnabled()
            window.save_settings()
            assert store.load_settings().tun is False
    finally:
        window.close()
        app.processEvents()


def test_application_icon_has_small_and_large_sizes():
    app = QApplication.instance() or QApplication([])
    icon = app_icon()
    assert not icon.isNull()
    sizes = {size.width() for size in icon.availableSizes()}
    assert 16 in sizes and 32 in sizes and max(sizes) >= 128
    assert not icon.pixmap(16, 16).isNull()
    app.processEvents()
