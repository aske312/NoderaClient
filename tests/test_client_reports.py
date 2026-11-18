import base64
import hashlib
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtWidgets import QApplication

from nodera import client_reports as reports
from nodera.models import Profile, Settings
from nodera.pro_provenance import TRUSTED_PRO_KEYS, digest, verification_record
from nodera.storage import Store
from nodera.subscription import fetch_profile
from nodera.ui import MainWindow


@pytest.fixture
def profile():
    value = Profile(
        "Verified PRO",
        "https://server.example/s/private-token?format=yaml",
        {
            "proxies": [
                {
                    "name": "node",
                    "type": "ss",
                    "server": "private.example",
                    "password": "private-password",
                }
            ]
        },
        datetime.now(timezone.utc).isoformat(),
        reports_version=1,
        reports_diagnostics=True,
    )
    value.pro_verification = verification_record(
        {"kid": next(iter(TRUSTED_PRO_KEYS)), "url_sha256": digest(value.url.encode())},
        value.config,
    )
    return value


def observation(**changes):
    return {
        "state": "connected",
        "mode": "proxy",
        "core_version": "Mihomo v1.19.33 windows",
        "network_check": "error",
        "issues": ["https_probe_failed"],
        **changes,
    }


def test_payload_projects_only_numeric_events_and_preserves_unknowns():
    events = [
        {
            "time": "2026-11-16T10:00:00+00:00",
            "event": "traffic",
            "upload": 23,
            "download": True,
            "delta_download": 5,
            "host": "private.example",
            "process": "Secret.exe",
            "url": "https://server/s/private-token",
            "message": "password=private-password",
        }
    ]
    payload = reports.build_report(
        "session", {"upload": 42, "download": None, "connections": True}, observation(), events
    )
    assert payload["counters"] == {"upload": 42, "download": None, "connections": None}
    assert payload["diagnostics"]["core_version"] == "v1.19.33"
    assert payload["events"][0] == {
        "time": events[0]["time"],
        "event": "traffic",
        "upload": 23,
        "delta_download": 5,
    }
    assert not any(
        value in json.dumps(payload)
        for value in ("private.example", "Secret.exe", "private-token", "private-password")
    )
    assert reports.build_report("s", {}, observation(since=time.time()), events)["events"] == []


def test_transport_is_pro_only_same_origin_and_uses_existing_device_identity(profile):
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(200, json={"version": 1, "accepted": True, "duplicate": False})

    transport = httpx.MockTransport(receive)
    payload = reports.build_report("s", {}, observation())
    assert (
        reports.send_report(
            replace(profile, pro_verification={}), "device", payload, transport=transport
        )[0]
        == "unsupported"
    )
    assert requests == []
    assert reports.send_report(profile, "device", payload, transport=transport)[0] == "sent"
    assert str(requests[0].url) == "https://server.example/s/private-token/reports"
    assert requests[0].headers["X-Device-ID"] == "device"
    assert requests[0].headers["X-Client-Name"] == "Nodera"
    assert json.loads(requests[0].content) == payload
    assert (
        reports.send_report(
            replace(profile, url="https://other.example/s/private-token"),
            "device",
            payload,
            transport=transport,
        )[0]
        == "unsupported"
    )
    assert len(requests) == 1


def test_older_reports_v1_server_receives_only_its_supported_numeric_fields(profile):
    received = []
    transport = httpx.MockTransport(
        lambda request: (
            received.append(json.loads(request.content))
            or httpx.Response(200, json={"version": 1, "accepted": True})
        )
    )
    payload = reports.build_report("session", {"upload": 3}, observation())
    assert (
        reports.send_report(
            replace(profile, reports_diagnostics=False), "device", payload, transport=transport
        )[0]
        == "sent"
    )
    assert "diagnostics" not in received[0]
    assert received[0]["counters"]["upload"] == 3
    assert "diagnostics" in payload


@pytest.mark.parametrize(
    "status,expected",
    [(302, "unsupported"), (403, "unsupported"), (429, "limited"), (503, "unavailable")],
)
def test_redirects_limits_and_failures_never_repeat_a_post(profile, status, expected):
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(
            status, headers={"Location": "https://other.example/", "Retry-After": "45"}
        )

    state, delay = reports.send_report(
        profile, "device", {}, transport=httpx.MockTransport(receive)
    )
    assert state == expected
    assert delay == (45 if status == 429 else 30)
    assert len(requests) == 1


def test_capability_is_accepted_only_with_a_real_verified_pro_response():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    body = b"proxies:\n  - {name: node, type: ss, server: example.com, port: 443}\n"
    url = "https://pro.example/s/private"

    def receive(request):
        now = int(time.time())
        claims = {
            "v": 1,
            "source": "pro",
            "alg": "Ed25519",
            "kid": hashlib.sha256(public).hexdigest(),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "url_sha256": hashlib.sha256(str(request.url).encode()).hexdigest(),
            "nonce": request.headers["X-Subscription-Nonce"],
            "iat": now,
            "exp": now + 300,
        }
        encoded = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        proof = (
            encode(encoded) + "." + encode(key.sign(b"vps-control/pro-subscription/v1\0" + encoded))
        )
        return httpx.Response(
            200,
            content=body,
            headers={
                "X-Nodera-Reports-Version": "1",
                "X-Subscription-Proof": proof,
                "X-Subscription-Public-Key": encode(public),
            },
        )

    valid = fetch_profile(url, "PRO", "device", transport=httpx.MockTransport(receive))
    assert valid.reports_version == 1 and reports.supported(valid)
    plain = fetch_profile(
        url,
        "Standard",
        "device",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=body,
                headers={"X-Nodera-Reports-Version": "1", "X-Subscription-Source": "pro"},
            )
        ),
    )
    assert plain.reports_version == 0 and not reports.supported(plain)


def test_worker_is_opt_in_bounded_changes_session_on_restart_and_discards_disabled_pending(profile):
    sent, release = threading.Event(), threading.Event()
    values = []

    def sender(target, device, payload, **kwargs):
        values.append((target.url, payload))
        sent.set()
        release.wait(2)
        return "sent", 30

    publisher = reports.ConnectionReporter(sender)
    settings = Settings()
    try:
        publisher.observe(profile, settings, "core-1", {}, observation())
        assert publisher.thread is None
        settings = replace(settings, client_reports={profile.id: True})
        publisher.observe(profile, settings, None, {}, observation(state="disconnected"))
        assert publisher.thread is None
        publisher.observe(profile, settings, "core-1", {"upload": 1}, observation())
        assert sent.wait(2)
        for count in range(5):
            publisher.observe(profile, settings, "core-1", {"upload": count + 2}, observation())
        assert publisher.pending[3]["counters"]["upload"] == 6
        assert publisher.pending[3]["session_id"] == values[0][1]["session_id"]
        publisher.observe(profile, settings, "core-2", {"upload": 0}, observation())
        assert publisher.pending[3]["session_id"] != values[0][1]["session_id"]
        publisher.observe(
            profile, replace(settings, client_reports={}), "core-2", {}, observation()
        )
        assert publisher.pending is None
        release.set()
    finally:
        release.set()
        publisher.close()
        if publisher.thread:
            publisher.thread.join(2)
    assert len(values) == 1


def test_unknown_delivery_uses_a_new_observation_not_an_automatic_replay(profile):
    done = threading.Event()
    sent = []

    def sender(target, device, payload, **kwargs):
        sent.append(payload)
        done.set()
        return "unknown", 30

    publisher = reports.ConnectionReporter(sender)
    settings = Settings(client_reports={profile.id: True})
    try:
        publisher.observe(profile, settings, 1, {}, observation())
        assert done.wait(2)
        with publisher.condition:
            assert publisher.condition.wait_for(lambda: publisher.status == "unknown", timeout=2)
        done.clear()
        publisher.observe(profile, settings, 1, {"upload": 12}, observation())
        with publisher.condition:
            publisher.next_at = 0
            publisher.condition.notify_all()
        assert done.wait(2)
        assert sent[0]["session_id"] == sent[1]["session_id"]
        assert sent[0]["report_id"] != sent[1]["report_id"]
    finally:
        publisher.close()
        publisher.thread.join(2)


def test_core_restart_keeps_the_device_send_interval(profile):
    publisher = reports.ConnectionReporter(lambda *args, **kwargs: ("sent", 30))
    settings = Settings(client_reports={profile.id: True})
    try:
        publisher.observe(profile, settings, "first", {}, observation())
        with publisher.condition:
            assert publisher.condition.wait_for(lambda: publisher.status == "sent", timeout=2)
            deadline = publisher.next_at
        publisher.observe(profile, settings, "restarted", {}, observation())
        assert publisher.next_at == deadline and publisher.next_at > time.monotonic()
    finally:
        publisher.close()
        publisher.thread.join(2)


def test_late_rejection_does_not_block_a_refreshed_subscription(profile):
    started, release = threading.Event(), threading.Event()
    sent = []

    def sender(target, device, payload, **kwargs):
        sent.append((target, payload))
        if len(sent) == 1:
            started.set()
            assert release.wait(2)
            return "unsupported", 30
        return "sent", 30

    publisher = reports.ConnectionReporter(sender)
    settings = Settings(client_reports={profile.id: True})
    try:
        publisher.observe(profile, settings, 1, {"upload": 1}, observation())
        assert started.wait(2)
        updated = replace(
            profile,
            pro_verification={
                **profile.pro_verification,
                "verified_at": profile.pro_verification["verified_at"] + 1,
            },
        )
        publisher.observe(updated, settings, 1, {"upload": 2}, observation())
        release.set()
        with publisher.condition:
            assert publisher.condition.wait_for(lambda: publisher.next_at > 0, timeout=2)
            assert publisher.pending is not None
            publisher.next_at = 0
            publisher.condition.notify_all()
            assert publisher.condition.wait_for(lambda: publisher.status == "sent", timeout=2)
        assert sent[1][0] == updated
        assert sent[1][1]["counters"]["upload"] == 2
        assert sent[1][1]["session_id"] == sent[0][1]["session_id"]
    finally:
        release.set()
        publisher.close()
        publisher.thread.join(2)


def test_connected_window_waits_for_counters_and_cancels_on_subscription_denial(profile, tmp_path):
    settings = Settings(client_reports={profile.id: True})
    window = MainWindow(Store(tmp_path), settings, profile)
    sent = []

    def sender(target, device, payload, **kwargs):
        sent.append(payload)
        return "sent", 30

    publisher = window.client_reporter
    publisher.sender = sender
    window.set_connected("v1.19.33")
    assert publisher.thread is None
    data = {"report_counters": {"upload": 12, "download": 34, "connections": 1}, "uptime": 5}
    window.report_client_state("connected", data)
    with publisher.condition:
        assert publisher.condition.wait_for(lambda: publisher.status == "sent", timeout=2)
    assert sent[0]["counters"] == data["report_counters"]
    window.report_client_state("connected", data)
    assert publisher.pending is not None
    window.profile = replace(profile, access_state="denied")
    window.library = replace(window.library, profiles=[window.profile])
    window.refresh_profile()
    assert not window.report_sharing.isEnabled()
    assert publisher.pending is None
    assert publisher.snapshot()[0] == "disabled"


def test_reporting_is_a_pro_only_setting_saved_through_apply(profile, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(), profile)
    monkeypatch.setattr(window, "report_client_state", lambda *args, **kwargs: None)
    assert "diagnostics" not in window.channel_dialog.options
    assert not window.report_sharing.setting_row.isHidden()
    assert window.report_sharing.isEnabled()
    window.report_sharing.setChecked(True)
    assert window.store.load_settings().client_reports == {}
    window.save_settings()
    assert window.store.load_settings().client_reports == {profile.id: True}
    assert not window.connected and not window.core.running
    assert "api_key" not in window.settings.__dict__
    plain = replace(profile, pro_verification={})
    window.profile = plain
    window.library = replace(window.library, profiles=[plain])
    window.refresh_profile()
    assert window.report_sharing.setting_row.isHidden()
    assert not window.report_sharing.isEnabled()
    window.save_settings()
    assert window.store.load_settings().client_reports == {profile.id: True}
    window.close()
    app.processEvents()


def test_active_subscription_switch_does_not_apply_a_previous_profiles_unsaved_choice(
    profile, tmp_path, monkeypatch
):
    from nodera.models import ProfileLibrary

    second = replace(profile, id="other-local-profile", name="Other PRO")
    library = ProfileLibrary([profile, second], profile.id)
    window = MainWindow(
        Store(tmp_path), Settings(client_reports={profile.id: True}), profile, library=library
    )
    monkeypatch.setattr(window, "report_client_state", lambda *args, **kwargs: None)
    assert window.report_sharing.isChecked()
    window.report_sharing.setChecked(False)
    window.library.active_id = second.id
    window.profile = second
    window.refresh_profile()
    assert not window.report_sharing.isChecked()
    window.save_settings()
    assert window.store.load_settings().client_reports == {profile.id: True, second.id: False}
    window.close()
