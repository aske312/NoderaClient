import json
import os
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from nodera.dialogs import SubscriptionDialog
from nodera.models import ClientError, Profile, ProfileLibrary, Settings
from nodera.storage import Store, protect
from nodera.ui import MainWindow


def profile(name="A"):
    return Profile(
        name,
        f"https://example.com/s/secret-{name}",
        {"proxies": [{"name": name, "type": "ss"}], "tun": {"enable": False}},
        "2020-01-01T00:00:00+00:00",
    )


def wait(window, app):
    deadline = time.monotonic() + 6
    while (
        window.task or window.pending_connect or window.inspections
    ) and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    app.processEvents()
    assert not window.task and not window.pending_connect


def test_legacy_profile_migrates_once_to_an_encrypted_library(tmp_path):
    old = asdict(profile())
    old.pop("id")
    old.pop("route_overrides")
    (tmp_path / "profile.dpapi").write_bytes(protect(json.dumps(old).encode()))
    store = Store(tmp_path)
    library = store.load_library()
    assert len(library.profiles) == 1
    assert library.active.url == old["url"]
    assert Store(tmp_path).load_profile().id == library.active.id
    assert b"secret-A" not in (tmp_path / "profiles.dpapi").read_bytes()
    assert not (tmp_path / "profile.dpapi").exists()


def test_failed_migration_preserves_legacy_data(tmp_path, monkeypatch):
    ciphertext = protect(json.dumps(asdict(profile())).encode())
    (tmp_path / "profile.dpapi").write_bytes(ciphertext)

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("nodera.storage.atomic_write", fail)
    with pytest.raises(OSError):
        Store(tmp_path).load_library()
    assert (tmp_path / "profile.dpapi").read_bytes() == ciphertext
    assert not (tmp_path / "profiles.dpapi").exists()


def test_library_remembers_all_subscriptions_and_active_one(tmp_path):
    store = Store(tmp_path)
    first, second = profile("A"), profile("B")
    store.save_library(ProfileLibrary([first, second], second.id))
    assert Store(tmp_path).load_profile() == second
    store.save_profile(replace(second, name="Changed"))
    assert Store(tmp_path).load_library().profiles[0] == first
    store.delete_profile()
    assert Store(tmp_path).load_profile() == first
    store.delete_profile()
    assert Store(tmp_path).load_profile() is None


def test_ui_imports_many_switches_and_keeps_routes_without_showing_links(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    window = MainWindow(store, Settings(update_hours=0), None)
    window.timer.stop()
    monkeypatch.setattr(
        "nodera.profiles_ui.fetch_profile",
        lambda url, name, device, **kwargs: replace(profile(name), url=url),
    )
    window.load_subscription("https://example.com/s/secret-A", "A")
    wait(window, app)
    first = window.profile
    window.profile = replace(
        first,
        route_overrides={
            "A": [{"role": "relay", "country": "RU"}, {"role": "server", "country": "DE"}]
        },
    )
    window.refresh_profile()
    store.save_library(window.library)
    window.load_subscription("https://example.com/s/secret-B", "B")
    wait(window, app)
    assert len(window.library.profiles) == 2
    assert window.profile.name == "B"
    window.switch_profile(first.id)
    wait(window, app)
    assert window.profile.name == "A"
    assert window.profile.route_overrides["A"][0]["country"] == "RU"
    window.update_profile()
    wait(window, app)
    assert len(window.library.profiles) == 2
    assert window.profile.id == first.id
    assert window.profile.route_overrides["A"][1]["country"] == "DE"
    assert not any("secret" in field.text() for field in window.findChildren(QLineEdit))
    assert not hasattr(window, "url")
    assert store.load_library().active_id == first.id
    window.delete_profile()
    wait(window, app)
    assert window.profile.name == "B" and len(window.library.profiles) == 1
    window.close()
    app.processEvents()


def test_switch_while_connected_stops_old_core_then_reconnects(tmp_path, monkeypatch):
    monkeypatch.setattr("nodera.ui.is_admin", lambda: True)
    app = QApplication.instance() or QApplication([])
    first, second = profile("A"), profile("B")
    library = ProfileLibrary([first, second], first.id)
    store = Store(tmp_path)
    store.save_library(library)
    window = MainWindow(store, Settings(update_hours=0), first, library)
    window.timer.stop()
    window.set_connected("test")
    calls = []
    monkeypatch.setattr(window.core, "stop", lambda *args, **kwargs: calls.append("stop"))
    monkeypatch.setattr(window.core, "check_connectivity", lambda: None)
    monkeypatch.setattr(
        window.core, "start", lambda selected, settings: calls.append(selected.name) or "test"
    )
    window.switch_profile(second.id)
    wait(window, app)
    assert calls == ["stop", "B"]
    assert window.connected and window.profile.id == second.id
    assert store.load_profile().id == second.id
    window.close()
    app.processEvents()


def test_adding_another_subscription_does_not_interrupt_current_connection(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    first = profile("A")
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), first)
    window.timer.stop()
    window.set_connected("test")
    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", lambda *args, **kwargs: profile("B"))
    window.load_subscription(profile("B").url, "B")
    wait(window, app)
    assert len(window.library.profiles) == 2
    assert window.profile.id == first.id and window.connected
    window.close()
    app.processEvents()


def test_link_is_plain_text_only_in_the_one_time_add_dialog():
    app = QApplication.instance() or QApplication([])
    dialog = SubscriptionDialog()
    assert dialog.url.echoMode() == QLineEdit.EchoMode.Normal
    dialog.reject()
    app.processEvents()


def test_auto_refresh_of_inactive_subscription_does_not_change_active(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    first = replace(profile("A"), updated_at=datetime.now(timezone.utc).isoformat())
    second = profile("B")
    library = ProfileLibrary([first, second], first.id)
    window = MainWindow(Store(tmp_path), Settings(update_hours=1), first, library)
    window.timer.stop()
    window.subscription_timer.stop()
    calls = []

    def fetch(url, name, device, **kwargs):
        calls.append(name)
        return replace(second, updated_at=datetime.now(timezone.utc).isoformat())

    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", fetch)
    window.auto_update_profile()
    wait(window, app)
    assert calls == ["B"]
    assert window.profile.id == first.id
    assert window.library.active_id == first.id
    assert window.library.profiles[1].updated_at != second.updated_at
    window.close()
    app.processEvents()


def test_auto_update_save_failure_preserves_profile_and_delays_retry(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    original = profile("A")
    store = Store(tmp_path)
    store.save_library(ProfileLibrary([original], original.id))
    window = MainWindow(store, Settings(update_hours=1), original)
    window.timer.stop()
    window.subscription_timer.stop()
    monkeypatch.setattr(
        "nodera.profiles_ui.fetch_profile", lambda *args, **kwargs: profile("Updated")
    )

    def fail_save(*args):
        raise ClientError("Could not encrypt profile")

    monkeypatch.setattr(store, "save_library", fail_save)
    try:
        window.auto_update_profile()
        wait(window, app)
        assert window.profile == original
        assert Store(tmp_path).load_profile() == original
        assert window.update_retry_at > time.monotonic() + 850
        assert window.profile_retry_at[original.id] == window.update_retry_at
    finally:
        window.close()
        app.processEvents()


def test_manual_refresh_while_connected_preserves_session_and_uses_proxy(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    original = profile("A")
    original.config["proxies"][0]["server"] = "old.example"
    store = Store(tmp_path)
    store.save_library(ProfileLibrary([original], original.id))
    window = MainWindow(store, Settings(tun=False, update_hours=0, geo_auto=False), original)
    window.timer.stop()
    window.core.mixed_port = 12345
    window.set_connected("test")
    received = []

    def fetch(url, name, device, **kwargs):
        received.append(kwargs.get("proxy"))
        updated = profile("A")
        updated.config["proxies"][0]["server"] = "new.example"
        return updated

    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", fetch)
    monkeypatch.setattr(window.core, "apply_profile", lambda _: list(window.groups_data))
    monkeypatch.setattr(
        window.core, "stop", lambda *args: pytest.fail("Refresh interrupted connection")
    )
    monkeypatch.setattr(
        window.core, "start", lambda *args: pytest.fail("Refresh restarted connection")
    )
    try:
        assert window.update_button.isEnabled()
        window.update_profile()
        wait(window, app)
        assert received == ["http://127.0.0.1:12345"]
        assert window.connected
        assert window.profile.id == original.id
        assert store.load_profile().config["proxies"][0]["server"] == "new.example"
        assert window.display_profile().config["proxies"][0]["server"] == "new.example"
        window.set_disconnected()
        assert window.display_profile().config["proxies"][0]["server"] == "new.example"
    finally:
        window.close()
        app.processEvents()


def test_manual_save_failure_keeps_previous_profile_in_memory_and_disk(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    original = profile("A")
    store = Store(tmp_path)
    store.save_library(ProfileLibrary([original], original.id))
    window = MainWindow(store, Settings(update_hours=0), original)
    window.timer.stop()
    errors = []
    monkeypatch.setattr(
        "nodera.profiles_ui.fetch_profile", lambda *args, **kwargs: profile("Updated")
    )
    monkeypatch.setattr(window, "error", errors.append)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "save_library", fail)
    try:
        window.update_profile()
        wait(window, app)
        assert errors
        assert window.profile == original
        assert Store(tmp_path).load_profile() == original
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("automatic", [False, True])
def test_live_apply_failure_restores_saved_subscription(tmp_path, monkeypatch, automatic):
    app = QApplication.instance() or QApplication([])
    original = profile("A")
    store = Store(tmp_path)
    store.save_library(ProfileLibrary([original], original.id))
    window = MainWindow(store, Settings(tun=False, geo_auto=False), original)
    window.timer.stop()
    window.subscription_timer.stop()
    window.set_connected("test")
    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", lambda *a, **kw: profile("Changed"))

    def reject(_):
        raise ClientError("Reload failed")

    monkeypatch.setattr(window.core, "apply_profile", reject)
    monkeypatch.setattr(window, "error", lambda _: None)
    try:
        window.load_subscription(original.url, original.name, automatic=automatic)
        wait(window, app)
        assert window.connected and window.profile == original
        assert store.load_profile() == original
        assert window.subscription_state == "error"
    finally:
        window.close()
        app.processEvents()
