import copy
import threading
import time
from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest

from nodera import channel_selection as channels
from nodera.models import ClientError, Profile, ProfileLibrary


def profile():
    return Profile(
        "PRO",
        "https://312node.net/s/private-token",
        {
            "proxies": [{"name": "good", "type": "ss"}, {"name": "bad", "type": "ss"}],
            "proxy-groups": [{"name": "choice", "type": "select", "proxies": ["bad", "good"]}],
            "rules": ["MATCH,choice"],
        },
        "2020-01-01T00:00:00+00:00",
    )


def result(p=None):
    p = p or profile()
    return channels.SelectionResult(
        replace(p, config=channels.selected_config(p.config, ["good"])),
        [],
        "revision",
        time.time(),
        False,
        60,
    )


def test_command_url_preserves_origin_and_strips_import_hints():
    assert channels.command_url("https://host:443/s/token?client=nodera") == (
        "https://host:443/s/token/commands/select-channels"
    )
    assert channels.command_url("https://host/api/mihomo/subscriptions/token").endswith(
        "/api/mihomo/subscriptions/token/commands/select-channels"
    )
    with pytest.raises(ClientError):
        channels.command_url("https://host/admin")


def test_candidate_request_is_bounded_and_sends_hwid_without_accepting_redirect(monkeypatch):
    monkeypatch.setattr(channels, "windows_identity", lambda: ("PC", "11"))
    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers["X-Device-ID"] == "pc"
        assert request.headers["X-Client-Name"] == "Nodera"
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={
                "version": 1,
                "command": "select-channels",
                "config": "proxies: [{name: node, type: ss}]",
                "require_udp": False,
                "revision": "v1",
                "expires_at": int(time.time()) + 60,
            },
        )

    config, revision = channels.request_candidates(
        profile(), "pc", transport=httpx.MockTransport(handle)
    )
    assert config["proxies"][0]["name"] == "node" and revision == "v1"
    with pytest.raises(ClientError):
        channels.request_candidates(
            profile(),
            "pc",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(307, headers={"Location": "https://other/s/secret"})
            ),
        )
    assert len(seen) == 1


def test_filter_preserves_dialer_chain_server_rules_and_original():
    config = profile().config
    config["proxies"][0]["dialer-proxy"] = "__312_transport_private"
    config["proxies"].append({"name": "__312_transport_private", "type": "ss"})
    before = copy.deepcopy(config)
    selected = channels.selected_config(config, ["good"])
    assert {n["name"] for n in selected["proxies"]} == {"good", "__312_transport_private"}
    assert selected["proxy-groups"][0]["proxies"] == ["good"]
    assert selected["rules"] == before["rules"] and config == before


def test_sustained_probe_requires_duration_and_repeated_transfers(monkeypatch):
    now = [0]
    monkeypatch.setattr(channels.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(channels, "transfer_probe", lambda _: channels.TRANSFER_BYTES)
    monkeypatch.setattr(channels, "udp_probe", lambda _: None)
    cancel = Mock()
    cancel.is_set.return_value = False

    def wait(seconds):
        now[0] += seconds
        return False

    cancel.wait.side_effect = wait
    core = Mock(mixed_port=1234)
    row = channels.sustained_probe(core, 60, True, cancel, lambda _: None)
    assert row["seconds"] >= 60 and row["transfers"] >= 3 and row["udp"]
    assert row["bytes"] == row["transfers"] * channels.TRANSFER_BYTES
    assert core.check_connectivity.call_count == row["transfers"]


def test_udp_requirement_and_cancellation_fail_closed(monkeypatch):
    monkeypatch.setattr(channels, "transfer_probe", lambda _: channels.TRANSFER_BYTES)
    monkeypatch.setattr(channels, "udp_probe", Mock(side_effect=OSError("no UDP")))
    with pytest.raises(ClientError, match="UDP"):
        channels.sustained_probe(Mock(mixed_port=1234), 30, True, threading.Event(), lambda _: None)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ClientError, match="отменён"):
        channels.sustained_probe(Mock(), 30, False, cancel, lambda _: None)


@pytest.mark.parametrize("failure", ["network", "disk"])
def test_explicit_apply_rolls_back_before_reporting_failure(monkeypatch, failure):
    p = profile()
    library = ProfileLibrary([p], p.id)
    core, store = Mock(), Mock()
    if failure == "network":
        core.check_connectivity.side_effect = ClientError("offline")
    else:
        store.save_library.side_effect = OSError("disk full")
    monkeypatch.setattr(channels, "transfer_probe", lambda _: channels.TRANSFER_BYTES)
    with pytest.raises(ClientError, match="Прежняя"):
        channels.apply_selection(result(p), p, library, store, core, True)
    assert core.apply_profile.call_count == 2
    assert core.apply_profile.call_args.args[0] == p
    if failure == "network":
        store.save_library.assert_not_called()
    assert library.active == p


def test_stale_result_never_changes_session_or_disk():
    p = profile()
    ready = result(p)
    ready.created_at -= 1801
    core, store = Mock(), Mock()
    with pytest.raises(ClientError, match="устарел"):
        channels.apply_selection(ready, p, ProfileLibrary([p], p.id), store, core, True)
    core.apply_profile.assert_not_called()
    store.save_library.assert_not_called()


def test_selection_is_isolated_and_does_not_apply_or_save(monkeypatch, tmp_path):
    p = profile()
    before = copy.deepcopy(p)
    core = Mock()
    monkeypatch.setattr(channels, "Core", Mock(return_value=core))
    monkeypatch.setattr(channels, "physical_interface", lambda **_: (1, "192.0.2.2", "Ethernet"))
    monkeypatch.setattr(
        channels, "request_candidates", lambda *a, **k: (copy.deepcopy(p.config), "v1")
    )
    monkeypatch.setattr(channels, "transfer_probe", lambda _: channels.TRANSFER_BYTES)
    monkeypatch.setattr(
        channels,
        "sustained_probe",
        Mock(
            side_effect=[
                {"state": "passed", "seconds": 60, "transfers": 5, "bytes": 1000, "udp": True},
                ClientError("failed"),
            ]
        ),
    )
    ready = channels.select_channels(p, "pc", tmp_path)
    assert p == before
    assert [n["name"] for n in ready.profile.config["proxies"]] == ["good"]
    core.apply_profile.assert_called_once_with(ready.profile)
    core.stop.assert_called_once()
    settings = core.start.call_args.args[1]
    assert not settings.tun and not settings.system_proxy
    assert not list(tmp_path.iterdir())


def test_auto_update_pauses_for_testing_and_pending_result():
    from nodera.profiles_ui import ProfilesMixin

    window = Mock(pending_channel_result=object(), channel_cancel=None)
    ProfilesMixin.auto_update_profile(window)
    window.load_subscription.assert_not_called()
    window.pending_channel_result = None
    window.channel_cancel = threading.Event()
    ProfilesMixin.auto_update_profile(window)
    window.load_subscription.assert_not_called()


@pytest.mark.parametrize("decision", ["accept", "reject", "local_failure"])
def test_modal_keeps_settings_progress_report_and_explicit_decisions(
    tmp_path, monkeypatch, decision
):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from nodera.models import Settings
    from nodera.storage import Store
    from nodera.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    p = profile()
    library = ProfileLibrary([p], p.id)
    store = Store(tmp_path)
    store.save_library(library)
    before = (tmp_path / "profiles.dpapi").read_bytes()
    ready = result(p)
    ready.source_digest = channels.config_digest(p.config)
    ready.rows = [
        {
            "name": "good",
            "state": "passed",
            "seconds": 60,
            "transfers": 3,
            "bytes": 786432,
            "udp": True,
        }
    ]
    monkeypatch.setattr(channels, "select_channels", lambda *a, **k: ready)
    actions = []

    def change(value, device_id, action=None, **kwargs):
        actions.append(action)
        value.server_state = (
            "pending" if action is None else "accepted" if action == "accept" else "rolled_back"
        )
        value.operation_id = "a" * 32
        value.restored_config = p.config
        return value

    monkeypatch.setattr(channels, "change_server_selection", change)
    window = MainWindow(store, Settings(geo_auto=False, update_hours=0), p, library)

    def wait():
        deadline = time.monotonic() + 5
        while window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert window.task is None

    try:
        dialog = window.channel_dialog
        dialog.open()
        assert dialog.isModal() and dialog.duration.isEnabled()
        dialog.start.click()
        assert dialog.isVisible()
        wait()
        assert window.pending_channel_result == ready
        assert window.profile == p
        assert (tmp_path / "profiles.dpapi").read_bytes() == before
        assert not window.update_button.isEnabled()
        assert dialog.isVisible() and not dialog.duration.isEnabled()
        assert "Профиль подготовлен" in dialog.status.text()
        assert "Успешно: 1" in dialog.report.toPlainText()
        if decision == "reject":
            dialog.discard.click()
            wait()
            assert window.pending_channel_result is None
            assert (tmp_path / "profiles.dpapi").read_bytes() == before
            assert actions == [None, "reject"]
            return
        if decision == "local_failure":
            monkeypatch.setattr(
                channels,
                "apply_selection",
                Mock(side_effect=ClientError("Не удалось применить профиль.")),
            )
            dialog.apply.click()
            wait()
            assert actions == [None, "accept", "rollback"]
            assert window.pending_channel_result is None
            assert (tmp_path / "profiles.dpapi").read_bytes() == before
            assert "Не удалось применить" in dialog.status.text()
            return
        dialog.apply.click()
        wait()
        assert window.pending_channel_result is None
        assert [n["name"] for n in store.load_library().active.config["proxies"]] == ["good"]
        assert window.update_button.isEnabled() and dialog.rollback.isVisible()
        dialog.rollback.click()
        wait()
        assert store.load_library().active.config == p.config
        assert actions == [None, "accept", "rollback"]
    finally:
        window.channel_dialog.reject()
        window.close()
        app.processEvents()


def test_lost_prepare_reply_reads_status_without_repeating_mutation(monkeypatch):
    import yaml

    ready = result()
    requests = []

    def handle(request):
        requests.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("reply lost", request=request)
        return httpx.Response(
            200,
            json={
                "version": 1,
                "operation_id": ready.operation_id,
                "state": "pending",
                "config": yaml.safe_dump(ready.profile.config),
            },
        )

    channels.change_server_selection(ready, "pc", transport=httpx.MockTransport(handle))
    assert ready.server_state == "pending" and requests == ["POST", "GET"]


def test_unknown_server_result_remains_pending_for_recheck():
    ready = result()

    def unavailable(request):
        raise httpx.ConnectError("offline", request=request)

    channels.change_server_selection(ready, "pc", transport=httpx.MockTransport(unavailable))
    assert ready.server_state == "unknown" and len(ready.operation_id) == 32


def test_subscription_refresh_after_restart_cannot_import_unaccepted_server_profile():
    from nodera.subscription import fetch_profile

    def handle(request):
        return httpx.Response(
            200,
            headers={"X-Nodera-Selection-State": "pending"},
            text="proxies: [{name: prepared, type: ss}]",
        )

    with pytest.raises(ClientError, match="ожидающий решения"):
        fetch_profile(
            "https://312node.net/s/test", "PRO", "pc", transport=httpx.MockTransport(handle)
        )
