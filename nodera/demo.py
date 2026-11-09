"""Offline subscriptions for the test build, isolated from live profiles."""

import time
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from nodera.channel_policy import channel_capabilities
from nodera.core import Core
from nodera.demo_core import DemoCore
from nodera.models import ClientError, Profile, ProfileLibrary, Settings
from nodera.test_scenarios import DEMO_PROFILES, TestScenarios, subscription_profile
from nodera.ui import MainWindow


def demo_library():
    profiles = []
    for pro in (True, False):
        nodes = []
        routes = (
            ("DE · Frankfurt", "vless"),
            ("NL · Amsterdam", "vless"),
            ("FI · Helsinki", "hysteria2"),
            ("FR · Paris", "tuic"),
            ("SE · Stockholm", "wireguard"),
            ("PL · Warsaw", "wireguard"),
        )
        for index, (name, protocol) in enumerate(routes if pro else routes[:3]):
            nodes.append(
                {
                    "name": name,
                    "type": protocol,
                    "server": f"192.0.2.{index + 1}",
                    "port": 443,
                    "x-nodera-route": [
                        {"role": "relay", "country": "TR", "host": f"198.51.100.{index + 1}"},
                        {"role": "cdn", "country": "GB", "host": f"203.0.113.{index + 1}"},
                        *(
                            [{"role": "relay", "country": "CH", "host": f"198.51.100.{index + 21}"}]
                            if index % 2
                            else []
                        ),
                        {"role": "server", "country": name[:2], "host": f"192.0.2.{index + 1}"},
                    ],
                    "uuid": "00000000-0000-4000-8000-000000000000",
                    "password": "demo-only",
                    "cipher": "aes-128-gcm",
                    "tls": protocol != "wireguard",
                    "udp": True,
                    **({"network": "xhttp" if index == 0 else "ws"} if protocol == "vless" else {}),
                }
            )
        profiles.append(
            Profile(
                "Демо PRO" if pro else "Демо Стандарт",
                "https://312node.net/s/demo-pro"
                if pro
                else "https://subscription.invalid/s/demo-standard",
                {
                    "proxies": nodes,
                    "proxy-groups": [
                        {
                            "name": "Авто" if pro else "Маршрут",
                            "type": "url-test" if pro else "select",
                            "proxies": [n["name"] for n in nodes],
                        }
                    ],
                    "rules": [
                        "DOMAIN-SUFFIX,example.invalid," + ("Авто" if pro else "Маршрут"),
                        "MATCH," + ("Авто" if pro else "Маршрут"),
                    ],
                    "dns": {"enable": True, "enhanced-mode": "fake-ip"},
                },
                datetime.now(timezone.utc).isoformat(),
                id="demo-pro" if pro else "demo-standard",
                channel_capabilities=channel_capabilities({"proxies": nodes}),
                usage={"upload": 128 * 1024**2, "download": 3 * 1024**3, "total": 100 * 1024**3},
            )
        )
    templates = {True: profiles[0], False: profiles[1]}
    fixtures = []
    for identity, definition in DEMO_PROFILES.items():
        profile = deepcopy(templates[definition["pro"]])
        profile = replace(
            profile,
            id=identity,
            name=definition["name"],
            url=profile.url
            if identity in {"demo-pro", "demo-standard"}
            else f"https://demo.invalid/s/{identity}",
        )
        fixtures.append(subscription_profile(profile, definition.get("subscription", "normal")))
    return ProfileLibrary(fixtures, fixtures[0].id)


def prepare_demo(store):
    library = store.load_library()
    fixtures = {p.id: p for p in demo_library().profiles}
    migrated = []
    for profile in library.profiles:
        fixture = fixtures.get(profile.id)
        if fixture:
            definitions = {n["name"]: n for n in fixture.config["proxies"]}
            nodes = profile.config.get("proxies", [])
            if profile.channel_capabilities != fixture.channel_capabilities or any(
                n["name"] in definitions
                and any(
                    n.get(key) != definitions[n["name"]].get(key)
                    for key in ("type", "network", "tls", "x-nodera-route")
                )
                for n in nodes
            ):
                profile = replace(
                    profile,
                    config={
                        **profile.config,
                        "proxies": [definitions.get(n["name"], n) for n in nodes],
                    },
                    channel_capabilities=channel_capabilities(fixture.config),
                )
        migrated.append(profile)
    changed = migrated != library.profiles
    library = ProfileLibrary(migrated, library.active_id)
    existing = {p.id for p in library.profiles}
    additions = [p for p in fixtures.values() if p.id not in existing]
    if additions or changed:
        store.save_library(
            ProfileLibrary(
                [*library.profiles, *additions],
                library.active_id or additions[0].id,
            )
        )
    if not (store.root / "settings.json").exists():
        store.save_settings(
            Settings(
                tun=False,
                system_proxy=False,
                geo_auto=False,
                update_hours=0,
                close_to_tray=False,
                client_location_attempted=True,
            )
        )


def is_demo_profile(profile):
    return profile is not None and profile.id in DEMO_PROFILES


class DemoWindow(MainWindow):
    def is_pro_profile(self, profile):
        if is_demo_profile(profile):
            return DEMO_PROFILES[profile.id]["pro"]
        return super().is_pro_profile(profile)

    def __init__(self, store, settings, profile, library=None):
        from nodera.demo_channels import DemoChannelService

        self.scenarios = TestScenarios(store.root)
        self.pending_test_scenarios = set()
        self.demo_channels = DemoChannelService(store.root, self.scenarios)
        core = (
            DemoCore(store.root / "runtime", self.scenarios)
            if is_demo_profile(profile)
            else Core(store.root / "runtime")
        )
        super().__init__(store, settings, profile, library, core=core)
        self.detect_client_button.clicked.disconnect()
        self.detect_client_button.clicked.connect(self.demo_location)
        from nodera.test_mode_ui import build_test_mode

        build_test_mode(self)
        self.refresh_test_access()
        self.log("Фиктивные подписки помечены «Демо»; реальные работают в обычном режиме.")

    @property
    def demo_mode(self):
        return is_demo_profile(self.profile)

    def select_core(self):
        if self.demo_mode != bool(getattr(self.core, "demo", False)):
            self.core.stop("subscription engine switch")
            self.core = (
                DemoCore(self.store.root / "runtime", self.scenarios)
                if self.demo_mode
                else Core(self.store.root / "runtime")
            )
            self.core.trace.configure(self.settings.log_limit, self.settings.save_logs)

    def toggle(self):
        if not self.connected and not self.task and not self.closing:
            self.select_core()
        super().toggle()

    def apply_test_scenario(self, identity):
        from nodera.test_mode_ui import apply_test_scenario

        apply_test_scenario(self, identity)

    def refresh_profile(self):
        super().refresh_profile()
        self.setWindowTitle("Nodera Test · Демо" if self.demo_mode else "Nodera Test")
        self.refresh_test_access()

    def refresh_test_access(self):
        if not hasattr(self, "test_page_index"):
            return
        button = self.navigation.buttons[self.test_page_index]
        button.setVisible(self.demo_mode)
        button.setEnabled(self.demo_mode)
        if self.demo_mode:
            self.test_profile.setCurrentIndex(self.test_profile.findData(self.profile.id))
        elif self.stack.currentIndex() == self.test_page_index:
            super().navigate(0)

    def navigate(self, index):
        if index == getattr(self, "test_page_index", None) and not self.demo_mode:
            index = 0
        super().navigate(index)

    def demo_location(self):
        if not self.demo_mode:
            from nodera.settings_ui import detect_point

            return detect_point(self)
        self.client_point_result.setText("Демо · местоположение задаётся вручную.")

    def channel_service(self, profile=None):
        return (
            self.demo_channels
            if is_demo_profile(profile or self.profile)
            else super().channel_service(profile)
        )

    def show_channel_result(self, result):
        super().show_channel_result(result)
        if not is_demo_profile(result.profile):
            return
        messages = {
            "pending": "Демо · профиль подготовлен. Принять изменения?",
            "accepted": "Демо · профиль принят. Доступен откат.",
            "rejected": "Демо · изменения отменены.",
            "rolled_back": "Демо · предыдущий профиль восстановлен.",
        }
        self.channel_status.setText(messages.get(result.server_state, "Демо · проверка завершена."))
        if result.rows:
            report = self.channel_dialog.report
            report.setPlainText("Имитация · трафик не передавался\n" + report.toPlainText())

    def fetch_subscription(self, url, name):
        profile = next((p for p in demo_library().profiles if p.url == url), None)
        if profile is None:
            return super().fetch_subscription(url, name)
        self.scenarios.check_subscription(profile.id, updating=True)
        return self.scenarios.apply_profile(replace(profile, name=name))

    def auto_client_location(self):
        if not self.demo_mode:
            return super().auto_client_location()

    def schedule_location_check(self, profile=None):
        if not self.demo_mode and not is_demo_profile(profile or self.profile):
            return super().schedule_location_check(profile)

    def check_locations(self, *args, **kwargs):
        if not self.demo_mode and not is_demo_profile(kwargs.get("profile_override")):
            return super().check_locations(*args, **kwargs)
        self.server_result.setText("Демо · адреса маршрутов фиктивные.")

    def test_group(self):
        if not self.demo_mode:
            return super().test_group()
        if not self.connected or self.closing or not self.profile:
            return
        now = time.time()
        self.delay_results.update(
            {
                (self.profile.id, name): {"value": self.core.delay(name), "checked_at": now}
                for name in self.core.node_names
            }
        )
        self.core.update_route_health(
            {name: self.delay_results[(self.profile.id, name)] for name in self.core.node_names}
        )
        self.record_route_results(
            self.profile.id,
            {name: self.delay_results[(self.profile.id, name)] for name in self.core.node_names},
        )
        self.set_groups(self.core.groups())
        self.update_network_tag()
        self.delay_status.setText("Демо · задержки имитируются")

    def connection_state_text(self):
        if not self.demo_mode:
            return super().connection_state_text()
        if not self.connected:
            return "Отключено"
        return "Нет доступа" if self.network_state == "error" else "Подключено"

    def check_connection(self):
        if not self.demo_mode:
            return super().check_connection()
        if not self.connected:
            return
        try:
            self.core.check_connectivity()
            self.network_state = "ok"
            self.network_error = ""
        except ClientError as exc:
            self.network_state = "error"
            self.network_error = str(exc)
        self.record_network_result(self.network_state == "ok")
        self.network_checked_wall = time.time()
        self.state.setText(self.connection_state_text())
        self.hint.setText(self.network_error or "Подключение проверено; трафик имитируется.")
        self.connection_hint = self.hint.text()
        self.update_network_tag()

    def diagnose(self):
        if not self.demo_mode:
            return super().diagnose()
        self.show_checks(
            [
                {
                    "title": "Демонстрационный стенд",
                    "state": "ok",
                    "detail": "Две фиктивные подписки. Реальное ядро и сеть не используются.",
                }
            ]
        )

    def diagnose_network(self):
        return self.diagnose() if self.demo_mode else super().diagnose_network()

    def check_app_updates(self):
        return None

    def install_app_update(self):
        return None
