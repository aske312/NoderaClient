"""Network/sleep observations and gradual recovery of an existing user session."""

import ipaddress
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QNetworkInformation, QNetworkInterface

from nodera.models import ClientError


def interface_signature(interfaces=None):
    result = []
    flags = QNetworkInterface.InterfaceFlag
    for interface in QNetworkInterface.allInterfaces() if interfaces is None else interfaces:
        if (
            not interface.flags() & flags.IsUp
            or not interface.flags() & flags.IsRunning
            or interface.flags() & flags.IsLoopBack
        ):
            continue
        name = interface.humanReadableName().casefold()
        if any(token in name for token in ("nodera", "mihomo", "wintun")):
            continue
        addresses = []
        for entry in interface.addressEntries():
            try:
                address = ipaddress.ip_address(entry.ip().toString().split("%")[0])
            except ValueError:
                continue
            if address.is_loopback or address.is_link_local or address.is_unspecified:
                continue
            # IPv6 privacy-address rotation isn't a network transition.
            addresses.append(
                str(address)
                if address.version == 4
                else str(
                    ipaddress.ip_network(f"{address}/{max(entry.prefixLength(), 0)}", strict=False)
                )
            )
        if addresses:
            result.append((interface.index(), tuple(sorted(set(addresses)))))
    return tuple(sorted(result))


class NetworkWatcher(QObject):
    changed = Signal(str, bool)

    def __init__(self, parent, snapshot=None):
        super().__init__(parent)
        self.snapshot = snapshot or interface_signature
        self.signature = self.snapshot()
        self.last_tick = time.monotonic()
        self.last_wall = time.time()
        self.information = None
        if QNetworkInformation.loadDefaultBackend():
            self.information = QNetworkInformation.instance()
            self.information.reachabilityChanged.connect(self.backend_changed)
            self.information.transportMediumChanged.connect(self.backend_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def available(self):
        if (
            self.information
            and self.information.reachability() == QNetworkInformation.Reachability.Disconnected
        ):
            return False
        return bool(self.snapshot())

    def backend_changed(self, *_):
        self.signature = self.snapshot()
        self.changed.emit("Изменилась сеть", self.available())

    def tick(self):
        now, wall = time.monotonic(), time.time()
        resumed = now - self.last_tick >= 15 or wall - self.last_wall >= 15
        self.last_tick, self.last_wall = now, wall
        signature = self.snapshot()
        changed = signature != self.signature
        self.signature = signature
        if resumed or changed:
            self.changed.emit("Выход из сна" if resumed else "Изменилась сеть", self.available())

    def native_message(self, message):
        event = wintypes.MSG.from_address(int(message))
        if event.message == 0x218 and event.wParam in (7, 18):  # WM_POWERBROADCAST, resume
            self.last_tick, self.last_wall = time.monotonic(), time.time()
            self.changed.emit("Выход из сна", self.available())


class NetworkRecoveryMixin:
    def setup_network_recovery(self):
        self.recovery_active = False
        self.recovery_attempts = 0
        self.recovery_api_failures = 0
        self.recovery_restarts = 0
        self.recovery_available = True
        self.recovery_reason = ""
        self.recovery_timer = QTimer(self)
        self.recovery_timer.setSingleShot(True)
        self.recovery_timer.timeout.connect(self.process_network_recovery)
        self.network_watcher = NetworkWatcher(self)
        self.network_watcher.changed.connect(self.network_changed)

    def cancel_network_recovery(self):
        self.recovery_active = False
        self.recovery_timer.stop()
        self.recovery_attempts = self.recovery_api_failures = self.recovery_restarts = 0

    def network_changed(self, reason, available):
        if self.closing or not self.connected or getattr(self.core, "demo", False):
            return
        self.recovery_available = available
        self.recovery_reason = reason
        self.recovery_active = True
        self.recovery_attempts = self.recovery_api_failures = 0
        # In-flight checks describe the old network, not the resumed one.
        self.session_generation += 1
        self.delay_results.clear()
        self.route_results = {}
        self.network_state = "unverified"
        self.network_error = ""
        self.network_checked_wall = 0.0
        self.network_results = []
        self.route_recovery_at = float("-inf")
        self.state.setText(self.connection_state_text())
        self.navigation_status.setText("Восстановление сети…" if available else "Ожидание сети…")
        self.update_network_tag()
        self.hint.setText("Восстановление сети…" if available else "Ожидание сети…")
        self.log(
            reason + (". Проверяем подключение." if available else ". Ожидаем доступную сеть.")
        )
        self.recovery_timer.start(1200)

    def request_network_recovery(self, reason):
        if self.closing or not self.connected or getattr(self.core, "demo", False):
            return
        if not self.recovery_active:
            self.recovery_active = True
            self.recovery_reason = reason
            self.recovery_attempts = self.recovery_api_failures = 0
            self.recovery_available = self.network_watcher.available()
            self.recovery_timer.start(2000)

    def process_network_recovery(self):
        if not self.recovery_active or self.closing or not self.connected:
            return
        if self.task or "network-recovery" in self.inspections:
            self.recovery_timer.start(500)
            return
        self.recovery_available = self.network_watcher.available()
        if not self.recovery_available:
            self.hint.setText("Ожидание сети…")
            self.recovery_timer.start(2000)
            return
        self.recovery_attempts += 1
        if self.recovery_api_failures >= 2 and self.recovery_restarts < 2:
            self.restart_after_network_failure()
            return
        generation, core = self.session_generation, self.core
        first = self.recovery_attempts == 1
        self.hint.setText("Восстановление подключения…")

        def check():
            if not core.recovery_status():
                return False, False
            if first:
                core.reset_route_observations()
                try:
                    core.flush_dns()
                except ClientError:
                    pass
            if self.closing or generation != self.session_generation:
                return True, False
            try:
                core.check_connectivity()
                return True, True
            except ClientError:
                pass
            try:
                core.recheck_routes()
            except ClientError:
                pass
            if self.closing or generation != self.session_generation:
                return True, False
            try:
                core.check_connectivity()
                return True, True
            except ClientError:
                return True, False

        def checked(result):
            if not self.recovery_active:
                return
            api, https = result
            if https:
                self.cancel_network_recovery()
                self.network_state = "ok"
                self.network_error = ""
                self.network_checked_at = time.monotonic()
                self.network_checked_wall = time.time()
                self.record_network_result(True)
                self.state.setText(self.connection_state_text())
                self.navigation_status.setText("HTTPS проверен")
                self.tray.setToolTip("Nodera · " + self.state.text())
                self.log("Подключение восстановлено без перезапуска ядра.")
                self.update_network_tag()
                QTimer.singleShot(0, self.periodic_delays)
                return
            self.recovery_api_failures = 0 if api else self.recovery_api_failures + 1
            self.network_state = "error"
            self.network_error = "Маршрут пока недоступен" if api else "Нет ответа от API ядра"
            self.update_network_tag()
            self.schedule_network_retry()

        def failed(message):
            if not self.recovery_active:
                return
            self.log(message, "warning")
            self.schedule_network_retry()

        self.start_inspection("network-recovery", check, checked, failed)
        self.inspections["network-recovery"].finished.connect(self.resume_pending_recovery)

    def resume_pending_recovery(self):
        if self.recovery_active and not self.closing and not self.recovery_timer.isActive():
            self.recovery_timer.start(500)

    def schedule_network_retry(self):
        if self.recovery_active and not self.closing:
            delay = (2, 5, 10, 20, 30)[min(max(self.recovery_attempts - 1, 0), 4)]
            self.hint.setText(f"Ожидание восстановления сети · повтор через {delay} с")
            self.recovery_timer.start(delay * 1000)

    def restart_after_network_failure(self):
        self.recovery_restarts += 1
        self.recovery_api_failures = 0
        core, profile, settings = self.core, self.profile, self.settings
        self.log("API или адаптер ядра не восстановился. Перезапускаем текущий сеанс.", "warning")

        def restart():
            core.stop("network recovery: API unavailable")
            if self.closing or not self.recovery_active:
                raise ClientError("Восстановление отменено.")
            return core.start(profile, settings)

        def done(version):
            self.cancel_network_recovery()
            self.set_connected(version, verify=True)

        def failed(message):
            self.log(message, "warning")
            self.schedule_network_retry()

        self.run_task(restart, done, failed)
