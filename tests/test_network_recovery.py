import ctypes
import threading
from ctypes import wintypes
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtCore import QElapsedTimer, QObject, QTimer
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtTest import QTest

from nodera.controller import Controller
from nodera.core import Core
from nodera.models import ClientError, Profile, Settings
from nodera.network_recovery import NetworkWatcher, interface_signature
from nodera.storage import Store
from nodera.ui import MainWindow


def wait(app, predicate):
    deadline = QElapsedTimer()
    deadline.start()
    while not predicate() and deadline.elapsed() < 5000:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


def window_fixture(tmp_path, monkeypatch, app):
    profile = Profile("test", "https://example.invalid/s/test", {"proxies": []}, "")
    window = MainWindow(
        Store(tmp_path),
        Settings(tun=False, update_hours=0, geo_auto=False, close_to_tray=False),
        profile,
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    window.connected = True
    monkeypatch.setattr(window, "periodic_delays", lambda: None)
    monkeypatch.setattr(window, "recover_routes", lambda **kw: None)
    monkeypatch.setattr(window, "test_group", lambda: None)
    return window


def test_network_watch_detects_sleep_and_changes_without_initial_event(
    monkeypatch, application_fonts
):
    owner = QObject()
    clock = [0.0]
    signature = [((1, ("192.168.1.2",)),)]
    monkeypatch.setattr("nodera.network_recovery.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("nodera.network_recovery.time.time", lambda: clock[0])
    watcher = NetworkWatcher(owner, lambda: signature[0])
    watcher.timer.stop()
    events = []
    watcher.changed.connect(lambda *args: events.append(args))
    clock[0] = 2
    watcher.tick()
    assert not events
    signature[0] = ()
    clock[0] = 4
    watcher.tick()
    assert events == [("Изменилась сеть", False)]
    signature[0] = ((2, ("10.0.0.2",)),)
    clock[0] = 6
    watcher.tick()
    assert events[-1] == ("Изменилась сеть", True)
    clock[0] = 60
    watcher.tick()
    assert events[-1] == ("Выход из сна", True)
    message = wintypes.MSG()
    message.message, message.wParam = 0x218, 18
    watcher.native_message(ctypes.addressof(message))
    assert events[-1] == ("Выход из сна", True)
    assert len(events) == 4


def test_interface_snapshot_ignores_own_tun_and_ipv6_privacy_rotation():
    flags = QNetworkInterface.InterfaceFlag

    def interface(name, identity, addresses, active=True):
        entries = [
            SimpleNamespace(
                ip=lambda value=value: SimpleNamespace(toString=lambda: value),
                prefixLength=lambda: 64,
            )
            for value in addresses
        ]
        return SimpleNamespace(
            flags=lambda: flags.IsUp | flags.IsRunning if active else flags.IsUp,
            humanReadableName=lambda: name,
            index=lambda: identity,
            addressEntries=lambda: entries,
        )

    first = interface("Wi-Fi", 1, ["192.168.1.2", "2001:db8::1", "fe80::1"])
    second = interface("Wi-Fi", 1, ["192.168.1.2", "2001:db8::2", "fe80::2"])
    own = interface("Nodera", 2, ["198.19.0.1"])
    down = interface("Ethernet", 3, ["10.0.0.2"], False)
    assert interface_signature([first, own, down]) == interface_signature([second])
    assert not interface_signature([own, down])


def test_recovery_soft_success_preserves_core_and_selectors(
    tmp_path, monkeypatch, application_fonts
):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    calls = []
    monkeypatch.setattr(window.core, "recovery_status", lambda: True)
    monkeypatch.setattr(window.core, "flush_dns", lambda: calls.append("dns"))
    monkeypatch.setattr(window.core, "check_connectivity", lambda: calls.append("https"))
    monkeypatch.setattr(window.core, "recheck_routes", lambda: calls.append("groups"))
    window.delay_results[(window.profile.id, "old")] = {"value": 5}
    generation = window.session_generation
    window.network_changed("Выход из сна", True)
    assert window.session_generation == generation + 1
    assert not window.delay_results
    window.process_network_recovery()
    wait(application_fonts, lambda: not window.inspections)
    assert calls == ["dns", "https"]
    assert not window.recovery_active and window.connected
    assert window.network_state == "ok"
    assert not window.core.process


def test_absent_network_waits_without_restarts_or_probe_attempts(
    tmp_path, monkeypatch, application_fonts
):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    monkeypatch.setattr(window.network_watcher, "available", lambda: False)
    window.network_changed("Изменилась сеть", False)
    window.process_network_recovery()
    assert window.recovery_attempts == window.recovery_restarts == 0
    assert not window.inspections
    assert window.recovery_timer.interval() == 2000
    assert "Ожидание сети" in window.hint.text()


def test_route_failure_retries_without_restarting_healthy_api(
    tmp_path, monkeypatch, application_fonts
):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    calls = []
    monkeypatch.setattr(window.core, "recovery_status", lambda: True)
    monkeypatch.setattr(window.core, "flush_dns", lambda: None)
    monkeypatch.setattr(window.core, "recheck_routes", lambda: calls.append("routes"))

    def unavailable():
        raise ClientError("Route unavailable")

    monkeypatch.setattr(window.core, "check_connectivity", unavailable)
    window.request_network_recovery("Ошибка маршрута")
    for expected_delay in (2000, 5000, 10000):
        window.process_network_recovery()
        wait(application_fonts, lambda: not window.inspections)
        assert window.recovery_timer.interval() == expected_delay
        assert window.recovery_api_failures == window.recovery_restarts == 0
    assert calls == ["routes"] * 3
    assert window.recovery_active and window.connected


def test_two_api_failures_allow_one_controlled_restart(tmp_path, monkeypatch, application_fonts):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    calls = []
    monkeypatch.setattr(window.core, "recovery_status", lambda: False)
    monkeypatch.setattr(window.core, "stop", lambda reason: calls.append("stop"))
    monkeypatch.setattr(window.core, "start", lambda p, s: calls.append("start") or "v1.19.34")
    monkeypatch.setattr(window, "set_connected", lambda version, **kw: calls.append(version))
    window.request_network_recovery("Нет ответа от API")
    for _ in range(2):
        window.process_network_recovery()
        wait(application_fonts, lambda: not window.inspections)
    assert not calls and window.recovery_api_failures == 2
    window.process_network_recovery()
    wait(application_fonts, lambda: window.task is None)
    assert calls == ["stop", "start", "v1.19.34"]
    assert not window.recovery_active


def test_explicit_disconnect_cancels_pending_recovery(tmp_path, monkeypatch, application_fonts):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    calls = []
    monkeypatch.setattr(window.core, "stop", lambda reason: calls.append("stop"))
    window.request_network_recovery("Выход из сна")
    window.toggle()
    wait(application_fonts, lambda: window.task is None)
    window.process_network_recovery()
    assert calls == ["stop"]
    assert not window.connected and not window.recovery_active
    assert not window.recovery_timer.isActive()


def test_stale_recovery_result_cannot_repair_new_network_state(
    tmp_path, monkeypatch, application_fonts
):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(window.core, "recovery_status", lambda: True)
    monkeypatch.setattr(window.core, "flush_dns", lambda: None)

    def probe():
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(window.core, "check_connectivity", probe)
    window.request_network_recovery("Выход из сна")
    try:
        window.process_network_recovery()
        wait(application_fonts, entered.is_set)
        window.network_changed("Изменилась сеть", False)
    finally:
        release.set()
    wait(application_fonts, lambda: not window.inspections)
    assert window.network_state == "unverified" and window.recovery_active


def test_recovery_restart_limit_prevents_restart_storm(tmp_path, monkeypatch, application_fonts):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    monkeypatch.setattr(window.core, "recovery_status", lambda: False)
    window.request_network_recovery("Нет ответа от API")
    window.recovery_restarts = 2
    window.recovery_api_failures = 2
    window.process_network_recovery()
    wait(application_fonts, lambda: not window.inspections)
    assert window.task is None and window.recovery_restarts == 2
    assert window.recovery_timer.isActive()


def test_sleep_event_does_not_connect_a_disconnected_client(
    tmp_path, monkeypatch, application_fonts
):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    window.connected = False
    window.network_changed("Выход из сна", True)
    assert not window.recovery_active and not window.recovery_timer.isActive()
    assert window.task is None and not window.inspections


def test_readiness_detects_missing_tun_address(tmp_path, monkeypatch):
    import ipaddress

    core = Core(tmp_path / "runtime")
    core._base_config = {"tun": {"enable": True}, "dns": {"fake-ip-range": "198.19.0.1/16"}}
    monkeypatch.setattr(Core, "running", property(lambda _: True))

    def handle(request):
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "v1.19.34"})
        return httpx.Response(200, json={"tun": {"enable": True}})

    monkeypatch.setattr(
        core,
        "_client",
        lambda: httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr("nodera.core.ipv4_assignments", lambda: [])
    assert not core.recovery_status()
    monkeypatch.setattr(
        "nodera.core.ipv4_assignments", lambda: [ipaddress.ip_interface("198.19.0.1/16")]
    )
    assert core.recovery_status()


def test_network_change_clears_failure_streaks_without_unfreezing_or_changing_policy(tmp_path):
    core = Core(tmp_path / "runtime")
    core.route_health.failures = {"a": 2}
    core.route_health.last_checked = {"a": 100}
    core.route_health.frozen = {"frozen": 15}
    core.forced_server = "manual"
    core._traffic_sample = (0, 10, 20)
    core.reset_route_observations()
    assert not core.route_health.failures
    assert core.route_health.last_checked == {"a": 100}
    assert core.route_health.frozen == {"frozen": 15}
    assert core.forced_server == "manual" and core._traffic_sample is None


def test_old_probe_cannot_unfreeze_a_route_in_a_new_session(tmp_path, monkeypatch):
    from nodera.route_health import RouteHealth

    core = Core(tmp_path / "runtime")
    monkeypatch.setattr(Core, "running", property(lambda _: True))
    old = core.route_health
    old.frozen["node"] = 10
    core.route_health = RouteHealth()
    core.route_health.frozen["node"] = 20
    core.update_route_health({"node": {"value": 10}}, expected=old)
    assert core.route_health.frozen == {"node": 20}


def test_manual_delay_check_does_not_block_user_actions(tmp_path, monkeypatch, application_fonts):
    window = window_fixture(tmp_path, monkeypatch, application_fonts)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(window, "current_node", lambda: "slow")

    def delay(member):
        entered.set()
        assert release.wait(3)
        return 1

    monkeypatch.setattr(window.core, "delay", delay)
    try:
        window.test_server()
        wait(application_fonts, entered.is_set)
        assert window.task is None
        window.run_task(lambda: True, lambda _: window.server_result.setText("new selection"))
        wait(application_fonts, lambda: window.task is None)
    finally:
        release.set()
    wait(application_fonts, lambda: not window.inspections)
    assert window.server_result.text() == "new selection"


@pytest.mark.parametrize("operation", ["recheck", "delay", "quarantine"])
def test_background_remote_probe_does_not_lock_manual_selection(tmp_path, monkeypatch, operation):
    core = Core(tmp_path / "runtime")
    core.group_names, core.node_names = ["manual"], ["a", "b"]
    monkeypatch.setattr(Core, "running", property(lambda _: True))
    entered, release, selected = threading.Event(), threading.Event(), threading.Event()

    class Api:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def select(self, *a):
            selected.set()

        def groups(self, *a):
            return []

        def recheck_routes(self, *a):
            entered.set()
            assert release.wait(3)

        def delay(self, *a):
            self.recheck_routes()
            return 10

    monkeypatch.setattr("nodera.core.Controller", Api)
    if operation == "recheck":
        callback = core.recheck_routes
    elif operation == "delay":

        def callback():
            return core.delay("a")
    else:
        samples = {"a": {"value": 0}, "b": {"value": 10}}
        core.update_route_health(samples)
        core.update_route_health(samples)

        def callback():
            return core.update_route_health(samples)

    worker = threading.Thread(target=callback)
    selector = threading.Thread(target=lambda: core.select("manual", "b"))
    try:
        worker.start()
        assert entered.wait(2)
        selector.start()
        assert selected.wait(1), "Manual selection waited for a remote health check"
    finally:
        release.set()
        worker.join(3)
        if selector.ident:
            selector.join(3)
    assert not worker.is_alive() and not selector.is_alive()


def test_automatic_groups_are_probed_concurrently():
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    requests = []

    def handle(request):
        if request.url.path == "/proxies":
            return httpx.Response(
                200,
                json={
                    "proxies": {
                        "a": {"type": "Fallback", "all": []},
                        "b": {"type": "URLTest", "all": []},
                    }
                },
            )
        with lock:
            requests.append(request.url.path)
            if len(requests) == 2:
                entered.set()
        assert release.wait(3)
        return httpx.Response(200, json={})

    result = []
    with Controller(1234, "test", httpx.MockTransport(handle)) as controller:
        worker = threading.Thread(
            target=lambda: result.extend(controller.recheck_routes(["a", "b"]))
        )
        try:
            worker.start()
            assert entered.wait(2), "Group checks ran sequentially"
        finally:
            release.set()
            worker.join(3)
    assert result == ["a", "b"]
