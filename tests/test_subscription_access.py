import os
import time
from dataclasses import replace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.models import ClientError, Profile, ProfileLibrary, Settings
from nodera.profile_view import subscription_notice
from nodera.storage import Store
from nodera.subscription import SubscriptionUnavailable, fetch_profile
from nodera.ui import MainWindow


@pytest.mark.parametrize(
    "status,body,state",
    [
        (404, {}, "missing"),
        (410, {}, "missing"),
        (403, {"detail": "Срок подписки истёк"}, "expired"),
        (403, {"detail": "Объём трафика подписки исчерпан"}, "exhausted"),
        (403, {"detail": "Ссылка привязана к другому HWID"}, "denied"),
        (403, {"detail": {"private": "PRIVATE_TOKEN"}}, "denied"),
        (403, {"detail": "PRIVATE_TOKEN" * 1000}, "denied"),
    ],
)
def test_rejection_preserves_reason_without_exposing_server_body(status, body, state):
    with pytest.raises(SubscriptionUnavailable) as error:
        fetch_profile(
            "https://example.test/s/PRIVATE_TOKEN",
            "Test",
            "device",
            httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
        )
    assert error.value.state == state
    assert "PRIVATE_TOKEN" not in str(error.value)


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    first = Profile(
        "First", "https://example.test/first", {"proxies": [{"name": "A", "type": "direct"}]}, ""
    )
    second = replace(first, id="second", name="Second", url="https://example.test/second")
    store = Store(tmp_path)
    store.save_library(ProfileLibrary([first, second], first.id))
    window = MainWindow(store, Settings(tun=False, geo_auto=False, update_hours=0), first)
    for timer in window.findChildren(QTimer):
        timer.stop()
    monkeypatch.setattr(window, "error", Mock())
    window.core.stop = Mock()
    window.core.start = Mock()
    window.show()
    app.processEvents()
    try:
        yield app, window, first, second
    finally:
        window.connected = False
        window.exit_app()
        app.processEvents()


def wait(app, window):
    deadline = time.monotonic() + 5
    while window.task or window.inspections:
        assert time.monotonic() < deadline
        app.processEvents()
        QTest.qWait(5)
    app.processEvents()


@pytest.mark.parametrize("automatic", [False, True])
def test_deleted_subscription_warning_persists_until_successful_refresh(
    window, monkeypatch, automatic
):
    app, window, first, _ = window

    def rejected(*args, **kwargs):
        raise SubscriptionUnavailable("missing")

    monkeypatch.setattr(window, "fetch_subscription", rejected)
    window.load_subscription(first.url, first.name, automatic=automatic)
    wait(app, window)
    assert window.profile.access_state == "missing"
    assert window.store.load_profile().access_state == "missing"
    assert "удалена" in window.dashboard_access_notice.text()
    assert not window.dashboard_access_notice.isHidden()
    window.show_subscription_state(None)  # Transient feedback may disappear.
    window.refresh_profile()
    assert not window.dashboard_access_notice.isHidden()
    window.navigate(1)
    assert "удалена" in window.profile_access_notice.text()
    assert not window.profile_access_notice.isHidden()
    assert window.profile.config == first.config
    assert window.core.stop.call_count == 0

    monkeypatch.setattr(window, "fetch_subscription", lambda *a: first)
    window.load_subscription(first.url, first.name)
    wait(app, window)
    assert window.profile.access_state == ""
    assert window.store.load_profile().access_state == ""
    assert window.dashboard_access_notice.isHidden()
    assert window.profile_access_notice.isHidden()


def test_inactive_rejection_does_not_mark_or_disconnect_active_subscription(window, monkeypatch):
    app, window, first, second = window
    window.connected = True
    window.session_profile = first
    window.preview_profile_id = second.id

    def rejected(*args):
        raise SubscriptionUnavailable("expired")

    monkeypatch.setattr(window, "fetch_subscription", rejected)
    window.load_subscription(second.url, second.name, automatic=True)
    wait(app, window)
    assert window.library.active_id == first.id and window.profile.access_state == ""
    assert "истёк" in window.profile_access_notice.text()
    assert window.dashboard_access_notice.isHidden()
    assert window.core.stop.call_count == window.core.start.call_count == 0
    assert window.store.load_library().profiles[1].access_state == "expired"


def test_network_error_does_not_claim_subscription_was_removed(window, monkeypatch):
    app, window, first, _ = window

    def unavailable(*args, **kwargs):
        raise ClientError("Не удалось соединиться")

    monkeypatch.setattr(window, "fetch_subscription", unavailable)
    window.load_subscription(first.url, first.name, automatic=True)
    wait(app, window)
    assert window.profile.access_state == ""
    assert window.dashboard_access_notice.isHidden()
    assert window.store.load_profile().access_state == ""


def test_connection_failure_suggests_refresh_without_claiming_deletion(window):
    _, window, _, _ = window
    window.connection_failed("Нет ответа сети.")
    assert "Обновите подписку" in window.hint.text()
    assert window.profile.access_state == ""
    assert window.dashboard_access_notice.isHidden()


@pytest.mark.parametrize(
    "usage,word", [({"expire": 1}, "истёк"), ({"total": 100, "download": 100}, "исчерпан")]
)
def test_cached_expiry_and_quota_show_warning_without_waiting_for_network(window, usage, word):
    _, window, first, _ = window
    window.profile = replace(first, usage=usage)
    window.refresh_profile()
    assert word in window.dashboard_access_notice.text()
    assert word in window.profile_access_notice.text()
    assert subscription_notice(replace(first, usage={"expire": 0, "total": 0})) == ""
