import copy
import json
from dataclasses import asdict, replace
from unittest.mock import Mock

import httpx
import pytest
import yaml

from nodera import channel_selection as channels
from nodera.channel_policy import ChannelPolicy, channel_capabilities
from nodera.models import ClientError, Profile, Settings
from nodera.storage import Store


def test_filters_combine_and_preserve_protocol_security_semantics():
    policy = ChannelPolicy(protocols=["vless", "trojan"], network="ws", require_tls=True)
    assert policy.matches({"type": "vless", "network": "ws", "tls": True})
    assert policy.matches({"type": "trojan", "network": "ws"})
    assert not policy.matches({"type": "vless", "network": "ws", "tls": False})
    assert not policy.matches({"type": "ss", "network": "ws", "tls": True})
    assert not policy.matches({"type": "trojan"})
    assert ChannelPolicy(network="quic", require_tls=True).matches({"type": "hysteria2"})
    assert ChannelPolicy(network="udp").matches({"type": "wireguard"})
    assert not ChannelPolicy(require_tls=True).matches({"type": "wireguard"})
    assert ChannelPolicy(network="xhttp").matches({"type": "vless", "network": "xhttp"})


def test_mihomo_capabilities_preserve_full_source_after_channel_selection():
    config = {
        "proxies": [
            {"name": "vless", "type": "vless", "network": "xhttp"},
            {"name": "hy2", "type": "hysteria2"},
            {"name": "tuic", "type": "tuic"},
            {"name": "wg", "type": "wireguard"},
            {"name": "__312_transport_x", "type": "ss"},
        ]
    }
    config["proxies"][0]["dialer-proxy"] = "__312_transport_x"
    capabilities = channel_capabilities(config)
    assert capabilities == {
        "protocols": ["vless", "hysteria2", "tuic", "wireguard"],
        "networks": ["xhttp", "quic", "udp"],
    }
    selected = channels.selected_config(config, ["vless"])
    assert channel_capabilities(selected, capabilities) == capabilities


@pytest.mark.parametrize(
    "values",
    [
        {"max_channels": -1},
        {"max_channels": 65},
        {"max_channels": True},
        {"protocols": ["unknown"]},
        {"protocols": "vless"},
        {"protocols": ["ss", "ss"]},
        {"network": "unknown"},
        {"require_tls": 1},
    ],
)
def test_invalid_policy_rejected(values):
    with pytest.raises(ClientError):
        ChannelPolicy(**values).validate()


def test_settings_round_trip_and_invalid_preferences(tmp_path):
    store = Store(tmp_path)
    settings = Settings(channel_preferences={"profile": asdict(ChannelPolicy(3, ["vless"]))})
    store.save_settings(settings)
    assert store.load_settings() == settings
    data = json.loads((tmp_path / "settings.json").read_text())
    data["channel_preferences"]["profile"]["max_channels"] = 100
    (tmp_path / "settings.json").write_text(json.dumps(data))
    with pytest.raises(ClientError):
        store.load_settings()


@pytest.mark.parametrize("limit", [0, 1, 4])
def test_selection_skips_filtered_channels_and_counts_only_successes(tmp_path, monkeypatch, limit):
    config = {
        "proxies": [
            {"name": "skipped", "type": "ss"},
            {"name": "broken", "type": "vless", "tls": True},
            {"name": "good", "type": "vless", "tls": True, "dialer-proxy": "__312_transport_x"},
            {"name": "reserve", "type": "vless", "tls": True},
            {"name": "__312_transport_x", "type": "ss"},
        ],
        "proxy-groups": [{"name": "auto", "type": "url-test", "proxies": ["skipped", "good"]}],
        "rules": ["MATCH,auto"],
    }
    p = Profile("PRO", "https://312node.net/s/test", copy.deepcopy(config), "")
    core = Mock()
    monkeypatch.setattr(channels, "Core", Mock(return_value=core))
    monkeypatch.setattr(channels, "physical_interface", lambda **_: (1, "192.0.2.1", "Ethernet"))
    monkeypatch.setattr(channels, "request_candidates", lambda *a, **k: (config, "v1"))
    monkeypatch.setattr(channels, "transfer_probe", lambda _: 262144)
    probe = Mock(
        side_effect=[
            ClientError("failed"),
            {"state": "passed", "seconds": 60, "transfers": 3, "bytes": 786432, "udp": True},
            {"state": "passed", "seconds": 60, "transfers": 3, "bytes": 786432, "udp": True},
        ]
    )
    monkeypatch.setattr(channels, "sustained_probe", probe)
    ready = channels.select_channels(
        p,
        "pc",
        tmp_path,
        policy=ChannelPolicy(limit, ["vless"], require_tls=True),
    )
    assert p.config == config
    assert [row["state"] for row in ready.rows] == [
        "skipped",
        "failed",
        "passed",
        "skipped" if limit == 1 else "passed",
    ]
    assert probe.call_count == (2 if limit == 1 else 3)
    assert {n["name"] for n in ready.profile.config["proxies"]} == (
        {"good", "__312_transport_x"} if limit == 1 else {"good", "reserve", "__312_transport_x"}
    )
    assert ready.profile.config["rules"] == config["rules"]
    assert ready.profile.config["proxy-groups"][0]["proxies"] == ["good"]

    # Exercise the existing server wire contract: all granted channels are
    # reported, exclusions are non-passing, no new API fields are required.
    def handle(request):
        payload = json.loads(request.content)
        assert len(payload["reports"]) == 4
        assert payload["reports"][0] == {"name": "skipped", "state": "failed"}
        return httpx.Response(
            200,
            json={
                "version": 1,
                "operation_id": payload["operation_id"],
                "state": "pending",
                "config": yaml.safe_dump(ready.profile.config),
            },
        )

    channels.change_server_selection(ready, "pc", transport=httpx.MockTransport(handle))
    assert ready.server_state == "pending"


def test_empty_filter_does_not_start_core_or_prepare_server(tmp_path, monkeypatch):
    p = Profile(
        "PRO", "https://312node.net/s/test", {"proxies": [{"name": "ss", "type": "ss"}]}, ""
    )
    monkeypatch.setattr(channels, "request_candidates", lambda *a, **k: (p.config, "v1"))
    monkeypatch.setattr(channels, "physical_interface", lambda **_: (1, "192.0.2.1", "Ethernet"))
    core = Mock()
    monkeypatch.setattr(channels, "Core", core)
    with pytest.raises(ClientError, match="нет каналов"):
        channels.select_channels(p, "pc", tmp_path, policy=ChannelPolicy(protocols=["vless"]))
    core.assert_not_called()


def test_modal_passes_multiple_protocols_and_locks_parameters(monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    from nodera.channel_options import ChannelOptionsDialog

    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.settings = Settings()
    owner.profile = Profile(
        "PRO",
        "",
        {
            "proxies": [
                {"name": "vless", "type": "vless"},
                {"name": "vmess", "type": "vmess"},
                {"name": "trojan", "type": "trojan"},
            ]
        },
        "",
    )
    for method in (
        "start_channel_selection",
        "apply_ready_channels",
        "discard_ready_channels",
        "recheck_ready_channels",
        "rollback_ready_channels",
    ):
        setattr(owner, method, Mock())
    dialog = ChannelOptionsDialog(owner)
    assert dialog.pages.currentWidget() is dialog.overview
    assert dialog.protocols.count() == 3
    extra = QWidget()
    dialog.add_option("extra", "Another option", "Independent PRO option", extra)
    dialog.show_option("extra")
    assert dialog.pages.currentWidget() is extra
    dialog.show_overview()
    assert dialog.pages.currentWidget() is dialog.overview
    dialog.all_protocols.setChecked(False)
    dialog.start.click()
    owner.start_channel_selection.assert_not_called()
    for index in (0, 2):
        dialog.protocols.item(index).setCheckState(Qt.CheckState.Checked)
    dialog.channel_count.setValue(2)
    dialog.tls.setChecked(True)
    dialog.start.click()
    policy = owner.start_channel_selection.call_args.args[2]
    assert policy == ChannelPolicy(2, ["vless", "trojan"], require_tls=True)
    dialog.set_parameters_enabled(False)
    assert not dialog.protocols.isEnabled() and not dialog.channel_count.isEnabled()
    dialog.set_parameters_enabled(True)
    assert dialog.protocols.isEnabled()
    owner.settings.channel_preferences["p"] = asdict(ChannelPolicy(5, ["ss"], "ws"))
    dialog.load_policy(
        replace(
            owner.profile,
            id="p",
            config={"proxies": [{"name": "ss", "type": "ss", "network": "ws"}]},
        )
    )
    assert dialog.channel_count.value() == 5
    assert dialog.network.currentData() == "ws"
    assert not dialog.tls.isChecked()
    dialog.deleteLater()
    owner.deleteLater()
    app.processEvents()
