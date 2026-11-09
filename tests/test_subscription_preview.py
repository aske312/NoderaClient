import os
import time
from dataclasses import replace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.models import Profile, ProfileLibrary, Settings
from nodera.storage import Store
from nodera.ui import MainWindow


def wait(window, app):
    end = time.monotonic() + 5
    while window.task or window.inspections or window.pending_connect:
        assert time.monotonic() < end
        QTest.qWait(10)
        app.processEvents()


@pytest.fixture
def library_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    first = Profile(
        "Current",
        "https://example.com/s/first",
        {"proxies": [{"name": "A", "type": "ss"}]},
        "",
        id="first",
    )
    second = Profile(
        "Other PRO",
        "https://312node.net/s/second",
        {"proxies": [{"name": "B", "type": "vless"}]},
        "",
        id="second",
    )
    library = ProfileLibrary([first, second], first.id)
    # This fixture exercises presentation of two tiers; proof verification has its own tests.
    monkeypatch.setattr(
        MainWindow, "is_pro_profile", lambda self, value: bool(value and value.id == second.id)
    )
    store = Store(tmp_path)
    store.save_library(library)
    window = MainWindow(
        store,
        Settings(update_hours=0, geo_auto=False, close_to_tray=False, tun=False),
        first,
        library,
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    window.core.stop = Mock()
    window.core.start = Mock(return_value="test")
    window.core.apply_profile = Mock()
    window.show()
    window.navigate(1)
    app.processEvents()
    try:
        yield app, window, first, second
    finally:
        window.connected = False
        window.exit_app()
        app.processEvents()


def test_browsing_changes_only_card_until_activation(library_window):
    app, window, first, second = library_window
    before = (window.store.root / "profiles.dpapi").read_bytes()
    window.profile_list.setCurrentRow(1)
    app.processEvents()
    assert window.profile_summary_title.text() == second.name
    assert window.profile.id == window.library.active_id == first.id
    assert window.profile_selector.currentData() == first.id
    assert window.connections_profile_selector.currentData() == first.id
    assert window.profile_current_badge.isHidden()
    assert not window.profile_pro_badge.isHidden()
    assert window.home_pro_badge.isHidden()
    assert window.profile_activate_button.isVisible()
    assert window.core.stop.call_count == window.core.start.call_count == 0
    assert (window.store.root / "profiles.dpapi").read_bytes() == before
    window.refresh_profile()
    assert window.card_profile().id == second.id
    assert window.profile_list.currentItem().data(Qt.ItemDataRole.UserRole) == second.id
    window.profile_activate_button.click()
    wait(window, app)
    assert window.profile.id == window.store.load_library().active_id == second.id
    assert not window.profile_current_badge.isHidden()
    assert window.profile_activate_button.isHidden()


def test_inactive_card_actions_preserve_current_connection(library_window, monkeypatch):
    app, window, first, second = library_window
    window.set_connected("test", verify=False)
    window.profile_list.setCurrentRow(1)
    assert window.connected and window.session_profile.id == first.id
    assert window.delete_button.isEnabled()
    monkeypatch.setattr("nodera.modals.get_text", lambda *a, **k: ("Renamed", True))
    window.rename_profile_button.click()
    wait(window, app)
    assert window.card_profile().name == "Renamed"
    assert window.profile.id == window.store.load_library().active_id == first.id
    monkeypatch.setattr(
        window,
        "fetch_subscription",
        lambda *a: replace(second, name="Updated", usage={"total": 1024}),
    )
    window.update_button.click()
    wait(window, app)
    assert window.profile_summary_title.text() == "Updated"
    assert window.profile.id == window.store.load_library().active_id == first.id
    assert window.connected and window.session_profile.id == first.id
    window.core.apply_profile.assert_not_called()
    window.core.stop.assert_not_called()
    monkeypatch.setattr("nodera.modals.confirm", lambda *a, **k: True)
    window.delete_button.click()
    wait(window, app)
    assert window.connected and window.profile.id == first.id
    assert [p.id for p in window.store.load_library().profiles] == [first.id]
    assert not window.delete_button.isEnabled()
    window.core.stop.assert_not_called()


def test_explicit_activation_reconnects_only_after_button_press(library_window, monkeypatch):
    app, window, first, second = library_window
    window.set_connected("test", verify=False)
    monkeypatch.setattr(window, "check_connection", lambda: None)
    monkeypatch.setattr(window, "periodic_delays", lambda: None)
    monkeypatch.setattr(window, "capture_flows", lambda: None)
    window.profile_list.setCurrentRow(1)
    assert window.profile.id == first.id and window.connected
    window.core.stop.assert_not_called()
    window.profile_activate_button.click()
    wait(window, app)
    assert window.profile.id == second.id and window.connected
    window.core.stop.assert_called_once()
    assert window.core.start.call_args.args[0].id == second.id
