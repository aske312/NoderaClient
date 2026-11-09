import os
import sys
import threading
import time
import traceback
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QEvent,
    QSignalBlocker,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QLayout,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from nodera.chrome import FramelessWindowMixin
from nodera.core import Core, is_admin
from nodera.dialogs import RouteDialog
from nodera.elevation import launch_admin
from nodera.flow_monitor import FlowMonitor
from nodera.flows_ui import FlowsMixin
from nodera.geolocation import GeoClient
from nodera.journal import Journal
from nodera.locations import country
from nodera.modals import QMessageBox
from nodera.models import ClientError, visible_proxies
from nodera.navigation import NavigationDock
from nodera.presentation import PresentationMixin
from nodera.profile_view import effective_tun, policy_view
from nodera.profile_view import format_bytes as size_text
from nodera.profiles_ui import ProfilesMixin
from nodera.routes import route_for_node
from nodera.server_assets import node_countries
from nodera.theme import STYLE as STYLE
from nodera.widgets import app_icon, label
from nodera.workbench import WorkbenchMixin
from nodera.world_map import WorldCanvas


class Task(QThread):
    result = Signal(object)
    failed = Signal(str)
    debug = Signal(str)

    def __init__(self, callback, parent):
        super().__init__(parent)
        self.callback = callback

    def run(self):
        try:
            self.result.emit(self.callback())
        except ClientError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.debug.emit(traceback.format_exc())
            self.failed.emit("Операция не выполнена. Проверьте настройки, доступ к файлам и сети.")


class MainWindow(
    FramelessWindowMixin, ProfilesMixin, WorkbenchMixin, PresentationMixin, FlowsMixin, QMainWindow
):
    shutdown_complete = Signal()
    channel_progress = Signal(str)

    def __init__(self, store, settings, profile, library=None, *, core=None):
        super().__init__()
        self.store, self.settings, self.profile = store, settings, profile
        self.library = library if library is not None else store.load_library()
        if profile:
            self.library = self.library.upsert(profile)
        self.profile_retry_at = {}
        self.pending_connect = False
        self.core = core if core is not None else Core(store.root / "runtime")
        self.core.trace.configure(settings.log_limit, settings.save_logs)
        self.core.connection_log.configure(
            settings.save_connection_logs,
            settings.connection_log_mb,
            settings.connection_log_interval,
        )
        self.task = None
        self.poll_task = None
        self.inspections = {}
        self.geo_task = None
        self.pending_geo_profiles = {}
        self.geo_render_timer = QTimer(self)
        self.geo_render_timer.setSingleShot(True)
        self.geo_render_timer.timeout.connect(self.refresh_location_views)
        self.geo_results = {}
        self.delay_results = {}
        self.geo_client = GeoClient(store.root)
        self.journal = Journal(store.root, settings.log_limit, settings.save_logs)
        self.session_generation = 0
        self.quit_requested = False
        self.connected = False
        self.closing = False
        self.shutdown_event = threading.Event()
        self.shutdown_emitted = False
        self.groups_data = []
        self.flow_monitor = FlowMonitor(settings.flow_history_minutes * 60)
        self.api_failures = 0
        self.connection_hint = ""
        self.session_tun = False
        self.session_profile = None
        self.network_state = "unverified"
        self.network_error = ""
        self.network_checked_at = 0.0
        self.network_checked_wall = 0.0
        self.route_recovery_at = float("-inf")
        self.network_checks_enabled = False
        self.profile_policy = {}
        self.update_retry_at = 0.0
        self.setWindowTitle("Nodera")
        self.setWindowIcon(app_icon())
        self.setFixedSize(840, 620)
        root = QWidget()
        self.configure_shell(root)
        content = QVBoxLayout(root)
        content.setContentsMargins(12, 0, 12, 14)
        content.setSpacing(12)
        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        self.navigation = NavigationDock(self)
        self.navigation.selected.connect(self.navigate)
        self.nav = self.navigation.buttons
        self.navigation_status = label("Не подключено", "muted")
        self.navigation_status.setParent(self)
        self.navigation_status.hide()
        content.addWidget(self.navigation, 0, Qt.AlignmentFlag.AlignHCenter)
        self.build_connection()
        self.build_profile()
        self.build_settings()
        self.build_events()
        self.build_servers()
        self.build_diagnostics()
        self.build_routes()
        self.embed_settings_tools()
        self.build_application()
        self.build_flows()
        self.navigate(0)
        self.refresh_profile()
        self.log(
            "Nodera готова."
            if self.profile
            else "Nodera готова. Добавьте подписку для подключения."
        )
        self.timer = QTimer(self)
        self.timer.setInterval(self.settings.poll_seconds * 1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.delay_timer = QTimer(self)
        self.delay_timer.setInterval(15000)
        self.delay_timer.timeout.connect(self.periodic_delays)
        self.delay_timer.start()
        self.flow_timer = QTimer(self)
        self.flow_timer.timeout.connect(self.capture_flows)
        self.log_timer = QTimer(self)
        self.log_timer.setInterval(500)
        self.log_timer.timeout.connect(self.ingest_core_logs)
        self.log_timer.start()
        self.subscription_timer = QTimer(self)
        self.subscription_timer.setInterval(60000)
        self.subscription_timer.timeout.connect(self.refresh_subscription_notices)
        self.subscription_timer.timeout.connect(self.auto_update_profile)
        self.subscription_timer.start()
        if getattr(sys, "frozen", False):
            self.app_update_timer = QTimer(self)
            self.app_update_timer.setSingleShot(True)
            self.app_update_timer.timeout.connect(self.check_app_updates)
            self.app_update_timer.start(5000)
        QTimer.singleShot(1000, self.auto_update_profile)
        self.client_location_timer = QTimer(self)
        self.client_location_timer.setSingleShot(True)
        self.client_location_timer.timeout.connect(self.auto_client_location)
        self.client_location_timer.start(1500)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.messageClicked.connect(self.show_update_offer)
        self.tray.setToolTip("Nodera · отключено")
        menu = QMenu(self)
        show = QAction("Открыть Nodera", self)
        show.triggered.connect(self.reveal)
        menu.addAction(show)
        self.tray_toggle = QAction("Подключить", self)
        self.tray_toggle.triggered.connect(self.toggle)
        menu.addAction(self.tray_toggle)
        quit_action = QAction("Завершить", self)
        quit_action.triggered.connect(self.exit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: (
                self.reveal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.refresh_controls()
        QApplication.instance().installEventFilter(self)

        QTimer.singleShot(0, self.check_saved_profile_locations)

    def check_saved_profile_locations(self):
        if not self.closing:
            for profile in self.library.profiles:
                self.schedule_location_check(profile)

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.ToolTip
            and not self.closing
            and self.stack.currentIndex() in (4, 6, 8)
            and isinstance(watched, QWidget)
            and (watched is self or self.isAncestorOf(watched))
        ):
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def page(self, title, map_background=False):
        page = WorldCanvas() if map_background else QWidget()
        layout = QVBoxLayout(page)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        if title:
            layout.addWidget(label(title, "title"))
        if map_background:
            self.stack.addWidget(page)
            return layout
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        self.stack.addWidget(scroll)
        return layout

    def navigate(self, index):
        if index in self.navigation.subscription_pages and not self.library.profiles:
            index = 0
        if index in (3, 5):
            self.settings_tabs.setCurrentWidget(
                self.events_section if index == 3 else self.diagnostics_section
            )
            index = 2
        self.stack.setCurrentIndex(index)
        if index in (4, 6, 8):
            QToolTip.hideText()
        if index == 2:
            self.refresh_autostart()
        self.navigation.set_current(index)
        if hasattr(self, "timer"):
            self.timer.setInterval(
                (self.settings.flow_poll_seconds if index == 8 else self.settings.poll_seconds)
                * 1000
            )
        if index == 8:
            self.render_flows(force=True)
            if hasattr(self, "timer") and self.connected:
                self.capture_flows()

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def error(self, message):
        self.log(message, "error")
        QMessageBox.warning(self, "Nodera", message)

    def run_task(self, callback, success, failure=None):
        if self.task:
            return
        self.session_generation += 1
        self.task = Task(callback, self)
        self.task.debug.connect(lambda message: self.log(message, "debug", "Worker"))
        self.task.result.connect(success)
        self.task.failed.connect(
            lambda message: (
                self.log(message, "warning") if self.closing else (failure or self.error)(message)
            )
        )
        self.task.finished.connect(self.task_finished)
        self.refresh_controls()
        self.task.start()

    def task_finished(self):
        task = self.task
        self.task = None
        task.deleteLater()
        self.refresh_controls()
        if self.closing:
            QTimer.singleShot(0, self.close)
        elif self.pending_connect:
            QTimer.singleShot(0, self.connect_pending)
        elif (
            self.connected
            and self.network_checks_enabled
            and "connectivity" not in self.inspections
        ):
            QTimer.singleShot(0, self.check_connection)

    def connect_pending(self):
        if self.pending_connect and not self.closing:
            self.pending_connect = False
            self.toggle()

    def refresh_controls(self):
        if self.profile:
            self.library = self.library.upsert(self.profile)
        has_subscription = bool(self.library.profiles)
        self.navigation.set_has_subscription(has_subscription)
        self.open_profile.setVisible(has_subscription)
        self.dashboard_update_button.setVisible(has_subscription)
        if not has_subscription and self.stack.currentIndex() in self.navigation.subscription_pages:
            self.navigate(0)
        idle = self.task is None and not self.connected and not self.closing
        for widget in self.network_settings_controls:
            widget.setting_row.setEnabled(True)
            widget.setEnabled(idle)
        self.system_proxy_check.setEnabled(idle and self.connection_mode.currentData() == "proxy")
        tun = idle and self.connection_mode.currentData() == "tun"
        for field in (self.stack_setting, self.mtu_setting):
            field.setEnabled(tun)
        self.flush_dns_button.setting_row.setEnabled(True)
        self.flush_dns_button.setEnabled(self.connected and self.task is None and not self.closing)
        for field in self.network_settings_controls:
            field.setting_row.setEnabled(field.isEnabled())
        self.flush_dns_button.setting_row.setEnabled(self.flush_dns_button.isEnabled())
        self.save_button.setEnabled(self.task is None and not self.closing)
        self.import_button.setEnabled(self.task is None and not self.closing)
        self.profile_add.setEnabled(self.task is None and not self.closing)
        self.empty_profile_add.setEnabled(self.task is None and not self.closing)
        self.dashboard_update_button.setEnabled(
            self.task is None and not self.closing and self.profile is not None
        )
        self.refresh_channel_controls()
        self.refresh_card_controls()
        self.profile_list.setEnabled(self.task is None and not self.closing)
        self.profile_selector.setEnabled(self.task is None and not self.closing)
        self.connections_profile_selector.setEnabled(self.task is None and not self.closing)
        self.power.setEnabled(self.task is None and not self.closing and self.profile is not None)
        self.power.setChecked(self.connected)
        self.rules_apply_button.setEnabled(
            self.connected and self.task is None and not self.closing
        )
        self.refresh_rule_controls()
        self.power.setBusy(
            bool(
                self.task
                and self.state.text()
                in {"Подключение…", "Отключение…", "Завершение…", "Переключение…"}
            )
        )
        self.diagnostic_button.setEnabled(self.task is None and not self.closing)
        if hasattr(self, "tray_toggle"):
            self.tray_toggle.setText("Отключить" if self.connected else "Подключить")
            self.tray_toggle.setEnabled(
                self.task is None and not self.closing and self.profile is not None
            )
        self.refresh_server_controls()
        self.refresh_flow_controls()

    def auto_client_location(self):
        from nodera.settings_ui import detect_point

        if not self.closing and not self.settings.client_point:
            detect_point(self, automatic=True)

    def refresh_profile(self):
        self.refresh_library()
        if self.profile:
            profile = self.profile
            self.profile_title.setText(profile.name)
            try:
                date = (
                    datetime.fromisoformat(profile.updated_at).astimezone().strftime("%d.%m, %H:%M")
                )
            except ValueError:
                date = "—"
            details = f"{urlsplit(profile.url).hostname} · {date}"
            self.profile_details.setText(details)
            self.hint.setText("")
        else:
            self.profile_title.setText("Нет профиля")
            self.profile_details.setText("Добавьте подписку")
            self.hint.setText("")
        self.refresh_controls()
        self.refresh_policy()
        self.refresh_dashboard()
        if not self.connected:
            self.offline_groups()

    def offline_groups(self):
        groups = []
        if self.profile:
            nodes = {
                node["name"]: node.get("type", "—") for node in visible_proxies(self.profile.config)
            }
            types = {
                "select": "Selector",
                "fallback": "Fallback",
                "url-test": "URLTest",
                "load-balance": "LoadBalance",
            }
            for group in self.profile.config.get("proxy-groups", []):
                if group.get("hidden"):
                    continue
                members = [
                    {"name": name, "type": nodes.get(name, "Группа"), "delay": None}
                    for name in group.get("proxies", [])
                ]
                groups.append(
                    {
                        "name": group["name"],
                        "type": types.get(group.get("type"), group.get("type", "")),
                        "current": self.profile.selections.get(group["name"], ""),
                        "members": members,
                    }
                )
            if not groups:
                groups.append(
                    {
                        "name": "Подключения профиля",
                        "type": "Overview",
                        "current": "",
                        "members": [
                            {"name": name, "type": kind, "delay": None}
                            for name, kind in nodes.items()
                        ],
                    }
                )
        self.set_groups(groups)

    def set_groups(self, groups):
        previous = self.group_combo.currentData()
        scroll = self.node_list.verticalScrollBar().value()
        self.groups_data = groups
        self.connections_strategy_tag.setText(policy_view(self.display_profile())["strategy"])
        blocker = QSignalBlocker(self.group_combo)
        names = [group["name"] for group in groups]
        if names != [self.group_combo.itemData(index) for index in range(self.group_combo.count())]:
            self.group_combo.clear()
            for group in groups:
                self.group_combo.addItem(group["name"], group["name"])
        index = self.group_combo.findData(previous)
        if index >= 0:
            self.group_combo.setCurrentIndex(index)
        del blocker
        self.render_nodes()
        self.node_list.verticalScrollBar().setValue(scroll)
        self.update_route_pill()
        self.update_network_tag()

    def current_group(self):
        name = self.group_combo.currentData()
        return next((group for group in self.groups_data if group["name"] == name), None)

    def current_node(self):
        item = self.node_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item and not item.isHidden() else None

    def render_nodes(self, *_):
        previous = self.current_node()
        profile = self.display_profile()
        geo_results = self.geo_for_profile()
        definitions = (
            {node["name"]: node for node in visible_proxies(profile.config)} if profile else {}
        )
        choices = self.current_choices()
        blocker = QSignalBlocker(self.node_list)
        scroll = self.node_list.verticalScrollBar().value()
        group = self.current_group()
        if not group:
            self.node_list.clear()
        if group:
            current_routes = set()
            if self.profile and self.profile.forced_server:
                current_routes.add(self.profile.forced_server)
            else:
                current_routes.update(group.get("active", []))
                if group.get("current"):
                    current_routes.add(group["current"])
                elif group.get("type") == "LoadBalance":
                    current_routes.update(self.flow_monitor.active_routes)
            active_routes = current_routes | self.flow_monitor.active_routes
            members = list(group["members"])
            if self.server_sort.currentData() == "name":
                members.sort(key=lambda node: node["name"].casefold())
            elif self.server_sort.currentData() == "delay":
                members.sort(
                    key=lambda node: (
                        node["delay"]
                        if node.get("delay", 0) and node["delay"] > 0
                        else float("inf")
                    )
                )
            desired = [node["name"] for node in members]
            existing = {
                self.node_list.item(index).data(Qt.ItemDataRole.UserRole): self.node_list.item(
                    index
                )
                for index in range(self.node_list.count())
            }
            for name, item in list(existing.items()):
                if name not in desired:
                    self.node_list.takeItem(self.node_list.row(item))
                    del existing[name]
            for position, node in enumerate(members):
                measured = (
                    self.delay_results.get((self.profile.id, node["name"]))
                    if self.profile
                    else None
                )
                if measured:
                    node = {**node, "delay": measured["value"]}
                delay = (
                    "не проверен"
                    if node["delay"] is None
                    else f"{node['delay']} мс"
                    if node["delay"] > 0
                    else "нет ответа"
                )
                frozen = node["name"] in self.core.frozen_routes
                is_current = self.connected and not frozen and node["name"] in current_routes
                in_use = self.connected and not frozen and node["name"] in active_routes
                active = (
                    " · Заморожен"
                    if frozen
                    else " · Текущий"
                    if is_current
                    else " · В работе"
                    if in_use
                    else ""
                )
                item = existing.get(node["name"])
                if item is None:
                    item = QListWidgetItem()
                    self.node_list.insertItem(position, item)
                elif self.node_list.row(item) != position:
                    self.node_list.takeItem(self.node_list.row(item))
                    self.node_list.insertItem(position, item)
                geo = geo_results.get(node["name"], {})
                address = next((row for row in geo.get("rows", []) if row.get("ip")), {})
                place = address.get("country", "")
                geo_text = " ".join(
                    filter(None, [address.get("ip", ""), place, address.get("city", "")])
                )
                definition = definitions.get(node["name"], {})
                if not geo_text and definition.get("server"):
                    geo_text = str(definition["server"]) + (
                        ":" + str(definition["port"]) if definition.get("port") else ""
                    )
                codes = node_countries(profile, node["name"], geo, choices)
                text = f"{node['name']}\n{node['type']} ? {delay}{active}\n{geo_text}"
                if item.text() != text:
                    item.setText(text)
                item.setData(Qt.ItemDataRole.UserRole, node["name"])
                item.setData(
                    Qt.ItemDataRole.UserRole + 1,
                    {
                        **node,
                        "active": in_use,
                        "current": is_current,
                        "forced": bool(self.profile and self.profile.forced_server == node["name"]),
                        "can_force": bool(definition),
                        "frozen": frozen,
                        "retry_in": max(
                            0, int(self.core.frozen_routes.get(node["name"], 0) - time.monotonic())
                        ),
                        "type": definition.get("type", node["type"])
                        if node["name"] in self.core.frozen_routes
                        else node["type"],
                        "endpoint": geo_text,
                        "countries": codes,
                    },
                )
                item.setToolTip(
                    f"{node['name']} · {node['type']} · {delay}{active}\n{geo_text}\nСтраны: "
                    + (
                        " → ".join(
                            country(code)["name"] if country(code) else "Страна неизвестна"
                            for code in codes
                        )
                        or "не указаны"
                    )
                )
                if measured:
                    item.setToolTip(
                        item.toolTip()
                        + "\nПроверено: "
                        + datetime.fromtimestamp(measured["checked_at"]).strftime("%H:%M:%S")
                    )
                if node["name"] in self.core.frozen_routes:
                    item.setToolTip(
                        item.toolTip()
                        + "\nЗаморожен после трёх ошибок подряд. Успешная проверка сразу возвращает маршрут в работу."
                    )
                if node["name"] == (previous or group["current"]):
                    self.node_list.setCurrentItem(item)
            if self.node_list.currentRow() < 0 and self.node_list.count():
                self.node_list.setCurrentRow(0)
        del blocker
        if not group:
            self.node_list.clear()
        self.filter_nodes()
        self.node_list.verticalScrollBar().setValue(scroll)
        self.render_addresses()
        self.refresh_server_controls()

    def filter_nodes(self, *_):
        query = self.server_search.text().strip().casefold()
        for index in range(self.node_list.count()):
            item = self.node_list.item(index)
            item.setHidden(query not in item.text().casefold())
        self.refresh_server_controls()

    def refresh_server_controls(self):
        if not hasattr(self, "select_button"):
            return
        idle = self.task is None and not self.closing
        forced = self.profile.forced_server if self.profile else ""
        self.route_actions.setVisible(self.server_tabs.currentIndex() == 0)
        group = self.current_group()
        self.select_button.setVisible(bool(group and group["type"] == "Selector" and not forced))
        self.node_list.pin_enabled = idle
        self.node_list.viewport().update()
        frozen = self.current_node() in self.core.frozen_routes
        self.select_button.setEnabled(
            bool(
                idle
                and group
                and group["type"] == "Selector"
                and self.current_node()
                and not (self.profile and self.profile.forced_server)
                and not frozen
            )
        )
        self.delay_button.setEnabled(
            bool(idle and self.connected and self.profile and "delays" not in self.inspections)
        )
        self.refresh_servers_button.setEnabled(idle and self.connected)
        self.location_button.setEnabled(
            bool(
                idle
                and self.profile
                and self.current_node()
                and any(
                    node["name"] == self.current_node()
                    for node in visible_proxies(self.profile.config)
                )
            )
        )

    def choose_location(self):
        member = self.current_node()
        if self.task or self.closing or not self.profile or not member:
            return
        choices = {
            **self.profile.selections,
            **{group["name"]: group.get("current", "") for group in self.groups_data},
        }
        if self.groups_data:
            choices.update(self.groups_data[0].get("choices", {}))
        hops = route_for_node(self.display_profile(), member, choices)
        dialog = RouteDialog(hops, self, node_name=member)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        overrides = dict(self.profile.route_overrides)
        selected = dialog.route()
        if selected is None:
            overrides.pop(member, None)
        else:
            overrides[member] = selected
        locations = dict(self.profile.locations)
        locations.pop(member, None)
        profile = replace(self.profile, route_overrides=overrides, locations=locations)

        def saved(_):
            self.profile = profile
            self.update_route_pill()
            self.server_result.setText("Маршрут сохранён")

        self.run_task(lambda: self.store.save_profile(profile), saved)

    def reload_servers(self):
        if self.connected:
            self.run_task(self.core.groups, self.set_groups)

    def select_server(self):
        group, member = self.current_group(), self.current_node()
        if self.task or not group or not member or group["type"] != "Selector":
            return
        name = group["name"]
        profile = replace(self.profile, selections={**self.profile.selections, name: member})

        def apply():
            groups = self.core.select(name, member) if self.connected else None
            try:
                self.store.save_profile(profile)
                saved = True
            except (OSError, ClientError):
                saved = False
            return groups, saved

        def selected(result):
            groups, saved = result
            if saved:
                self.profile = profile
            if groups is None:
                self.offline_groups()
            else:
                self.set_groups(groups)
            text = f"Выбран: {member}"
            if not saved:
                text += " · Выбор не сохранён"
            self.server_result.setText(text)
            self.log(text)

        self.run_task(apply, selected)

    def toggle_server_pin(self, member):
        if self.task or self.closing or not self.profile:
            return
        if self.profile.forced_server == member:
            self.restore_server_policy()
        elif member not in self.core.frozen_routes and any(
            node.get("name") == member for node in visible_proxies(self.profile.config)
        ):
            self.force_server(member)

    def force_server(self, member=None):
        member = member or self.current_node()
        if self.task or self.closing or not self.profile or not member:
            return
        profile = replace(self.profile, forced_server=member)

        def apply():
            groups = self.core.force_server(member) if self.connected else None
            try:
                self.store.save_profile(profile)
                saved = True
            except OSError:
                saved = False
            return groups, saved

        def done(result):
            groups, saved = result
            self.profile = profile
            if groups is not None:
                self.set_groups(groups)
            else:
                self.offline_groups()
            self.server_result.setText(
                "Принудительно: " + member + ("" if saved else " · Не сохранено")
            )
            self.log("Включён принудительный маршрут: " + member)
            self.refresh_dashboard()

        self.run_task(apply, done)

    def restore_server_policy(self):
        if self.task or self.closing or not self.profile:
            return
        profile = replace(self.profile, forced_server="")

        def apply():
            groups = self.core.restore_policy() if self.connected else None
            try:
                self.store.save_profile(profile)
                saved = True
            except OSError:
                saved = False
            return groups, saved

        def done(result):
            groups, saved = result
            self.profile = profile
            if groups is not None:
                self.set_groups(groups)
            self.server_result.setText(
                "Правила подписки восстановлены" + ("" if saved else " · Не сохранено")
            )
            if groups is None:
                self.offline_groups()
            self.log("Принудительный маршрут отключён. Правила подписки восстановлены.")
            self.refresh_dashboard()

        self.run_task(apply, done)

    def test_server(self):
        member = self.current_node()
        if self.task or not self.connected or not member:
            return
        self.server_result.setText(f"Проверка HTTPS через «{member}»…")

        def tested(delay):
            self.server_result.setText(f"Доступен · {delay} мс")
            for group in self.groups_data:
                for node in group["members"]:
                    if node["name"] == member:
                        node["delay"] = delay
            self.render_nodes()
            self.log(f"HTTPS-проверка маршрута завершена: {delay} мс.")

        def failed(message):
            self.server_result.setText(message)
            self.log(message)
            for group in self.groups_data:
                for node in group["members"]:
                    if node["name"] == member:
                        node["delay"] = 0
            self.render_nodes()

        self.run_task(lambda: self.core.delay(member), tested, failed)

    def toggle(self):
        self.power.setChecked(self.connected)
        if self.task or self.closing:
            return
        if self.connected:
            self.state.setText("Отключение…")
            self.run_task(
                lambda: self.core.stop("user disconnect"), lambda _: self.set_disconnected()
            )
        elif self.profile:
            if self.settings.tun and not getattr(self.core, "demo", False) and not is_admin():
                try:
                    self.store.save_settings(self.settings)
                    if launch_admin(wait_for_pid=os.getpid()):
                        self.exit_app()
                    else:
                        self.hint.setText("Запуск TUN отменён. Можно выбрать Proxy в настройках.")
                except (ClientError, OSError) as exc:
                    self.error(
                        str(exc)
                        if isinstance(exc, ClientError)
                        else "Не удалось сохранить режим подключения."
                    )
                return
            self.state.setText("Подключение…")
            self.hint.setText("")
            self.run_task(
                lambda: self.core.start(self.profile, self.settings),
                lambda version: self.set_connected(version, verify=True),
                self.connection_failed,
            )

    def connection_state_text(self):
        mode = "VPN" if self.session_tun else "Прокси"
        status = {
            "unverified": "ядро запущено",
            "checking": "проверяем сеть…",
            "ok": "доступ есть",
            "error": "нет ответа сети",
        }[self.network_state]
        self.state.setToolTip(
            "Ядро запущено. Доступ в сеть проверяется HTTPS-запросом через Nodera; это не отдельная проверка перехвата всех приложений TUN."
        )
        self.update_network_tag()
        return f"{mode} · {status}"

    def update_network_tag(self):
        if not hasattr(self, "network_tag"):
            return
        import time

        from nodera.network_quality import current_route, network_quality

        route = current_route(self.display_profile(), self.groups_data)
        profile_id = self.profile.id if self.profile else ""
        history = (
            getattr(self, "route_results", {}).get((profile_id, route), [])
            if route
            else getattr(self, "network_results", [])
        )
        state, explanation = network_quality(
            self.connected,
            self.network_state,
            route,
            self.delay_results,
            profile_id,
            history,
            time.time(),
        )
        if self.connected and self.api_failures >= 3:
            state, explanation = "unverified", "Нет свежих данных от ядра."
        text = {
            "off": "Отключено",
            "unverified": "Не проверен",
            "checking": "Проверка…",
            "ok": "Включено",
            "error": "Нет ответа" if route else "Проблемы сети",
            "slow": "С задержкой",
            "unstable": "Нестабильно",
        }[state]
        self.network_tag.setText(text)
        self.network_tag.setProperty("state", state)
        self.network_tag.style().unpolish(self.network_tag)
        self.network_tag.style().polish(self.network_tag)
        self.network_tag.setFixedWidth(
            max(72, self.network_tag.fontMetrics().horizontalAdvance(text) + 20)
        )
        self.network_tag.setEnabled(self.connected and state != "checking" and not self.closing)
        checked_at = (
            self.delay_results.get((profile_id, route), {}).get("checked_at", 0)
            if route
            else self.network_checked_wall
        )
        stamp = (
            datetime.fromtimestamp(checked_at).strftime("%H:%M:%S")
            if checked_at
            else "ещё не выполнена"
        )
        self.network_tag.setToolTip(
            explanation
            + (f" Маршрут: {route}. Проверка: " if route else " HTTPS через Nodera · Проверка: ")
            + stamp
            + ". Нажмите для повторной проверки."
        )
        if self.connected:
            self.hint.setText(explanation)
        else:
            self.hint.setText(
                "Нажмите кнопку для подключения."
                if self.profile
                else "Добавьте подписку для подключения."
            )

    def record_route_results(self, profile_id, values):
        import time

        now = time.time()
        histories = getattr(self, "route_results", {})
        self.route_results = {
            key: [(stamp, ok) for stamp, ok in history if now - stamp <= 60]
            for key, history in histories.items()
        }
        for name, sample in values.items():
            history = self.route_results.setdefault((profile_id, name), [])
            stamp = sample["checked_at"]
            if not history or history[-1][0] < stamp:
                history.append((stamp, sample["value"] > 0))

    def check_route_status(self):
        self.test_group()
        self.check_connection()

    def record_network_result(self, success):
        import time

        now = time.time()
        self.network_results = [
            (stamp, result)
            for stamp, result in getattr(self, "network_results", [])
            if now - stamp <= 60
        ]
        self.network_results.append((now, success))

    def check_connection(self):
        if not self.connected or self.closing or "connectivity" in self.inspections:
            return
        import time

        self.network_checked_at = time.monotonic()
        self.network_state = "checking"
        self.state.setText(self.connection_state_text())
        self.navigation_status.setText("Проверка сети…")
        self.tray.setToolTip("Nodera · " + self.state.text())

        def checked(_):
            self.record_network_result(True)
            self.network_checked_wall = time.time()
            self.network_state = "ok"
            self.network_error = ""
            self.state.setText(self.connection_state_text())
            self.navigation_status.setText("HTTPS проверен")
            self.tray.setToolTip("Nodera · " + self.state.text())
            self.update_network_tag()
            self.log(
                "HTTPS-запрос через локальный прокси прошёл. Перехват TUN проверяется отдельно."
            )

        def failed(message):
            self.record_network_result(False)
            self.network_checked_wall = time.time()
            self.network_state = "error"
            self.network_error = message
            self.state.setText(self.connection_state_text())
            self.navigation_status.setText("Проверка не прошла")
            self.tray.setToolTip("Nodera · " + self.state.text())
            self.update_network_tag()
            self.hint.setToolTip(message)
            self.log(message, "warning")
            self.recover_routes()
            self.test_group()

        self.start_inspection("connectivity", self.core.check_connectivity, checked, failed)

    def recover_routes(self, proactive=False):
        import time

        if (
            not self.connected
            or self.closing
            or self.task
            or "route-recovery" in self.inspections
            or self.profile
            and self.profile.forced_server
            or time.monotonic() - self.route_recovery_at < 60
        ):
            return
        if not (
            any(
                group.get("type") in {"Fallback", "URLTest", "LoadBalance"}
                for group in self.groups_data
            )
            or self.profile
            and any(
                group.get("type") in {"fallback", "url-test", "load-balance"}
                for group in self.profile.config.get("proxy-groups", [])
            )
        ):
            return
        self.route_recovery_at = time.monotonic()
        if not proactive:
            self.hint.setText("Проверяем альтернативные маршруты…")

        def done(names):
            if self.connected and names:
                self.log("Проверены альтернативы автоматических групп. Выбор обновляет ядро.")
                QTimer.singleShot(0, self.check_connection)

        self.start_inspection(
            "route-recovery",
            self.core.recheck_routes,
            done,
            lambda message: self.log(message, "warning"),
        )

    def set_connected(self, version, verify=False):
        self.flow_monitor.clear()
        self.connected = True
        self.session_profile = deepcopy(self.profile)
        self.network_checks_enabled = verify
        self.route_recovery_at = float("-inf")
        self.session_tun = effective_tun(self.profile.config if self.profile else {}, self.settings)
        self.session_time.setText("00:00:00")
        self.refresh_session_visibility()
        self.api_failures = 0
        self.network_state = "unverified"
        self.network_error = ""
        self.network_checked_wall = 0.0
        self.network_results = []
        self.route_results = {}
        self.state.setText(self.connection_state_text())
        self.hint.setText(
            ""
            if self.session_tun
            else "Системный прокси включён"
            if self.settings.system_proxy
            else f"127.0.0.1:{self.core.mixed_port or self.settings.mixed_port}"
        )
        self.tray.setToolTip("Nodera · " + self.state.text())
        self.connection_hint = self.hint.text()
        self.log(
            "Ядро запущено. " + ("TUN включён." if self.session_tun else "Локальный прокси готов.")
        )
        if self.core.mixed_port:
            self.log(f"Локальный порт выбран автоматически: {self.core.mixed_port}.")
        self.refresh_controls()
        self.server_result.setText("")
        self.navigation_status.setText(self.state.text())
        self.update_route_pill()
        if verify:
            self.flow_timer.start(self.settings.flow_poll_seconds * 1000)
            QTimer.singleShot(0, self.capture_flows)
            self.schedule_location_check()
            self.check_connection()
            QTimer.singleShot(0, self.periodic_delays)

    def set_disconnected(self):
        self.flow_timer.stop()
        self.delay_results.clear()
        self.delay_status.setText("Задержка · Автопроверка после подключения, каждые 15 с")
        self.flow_monitor.clear()
        self.connected = False
        self.refresh_session_visibility()
        self.network_checked_wall = 0.0
        self.update_network_tag()
        self.render_flows(force=True)
        self.session_profile = None
        self.network_checks_enabled = False
        self.network_state = "unverified"
        self.network_error = ""
        self.state.setText("VPN отключён" if self.settings.tun else "Прокси отключён")
        self.hint.setText("")
        self.tray.setToolTip("Nodera · отключено")
        self.navigation_status.setText("Не подключено")
        self.session_time.setText("00:00:00")
        for value in self.stats:
            value.setText("—")
        for value in self.stat_rates:
            value.setText("—")
        self.offline_groups()
        self.server_result.setText("")
        self.log("Ядро остановлено.")
        self.refresh_controls()

    def connection_failed(self, message):
        self.set_disconnected()
        self.hint.setText("Нет подключения. Обновите подписку или проверьте сеть.")
        self.hint.setToolTip(message)
        self.error(message)

    def poll(self):
        if not self.connected or self.task or self.poll_task or self.closing:
            return

        def updated(data):
            if self.api_failures:
                self.hint.setText(self.network_error or self.connection_hint)
            self.api_failures = 0
            self.state.setText(self.connection_state_text())
            self.navigation_status.setText(
                {
                    "ok": "HTTPS проверен",
                    "checking": "Проверка сети…",
                    "error": "Проверка не прошла",
                    "unverified": "Сеть не проверена",
                }[self.network_state]
            )
            self.tray.setToolTip("Nodera · " + self.state.text())
            self.stats[0].setText(size_text(data["download"]))
            self.stats[1].setText(size_text(data["upload"]))
            self.stats[2].setText(str(data["connections"]))
            if not self.flow_timer.isActive():
                self.flow_monitor.update(data.get("flows", []))
            self.render_flows()
            self.stat_rates[0].setText(f"↓ {size_text(data['download_rate'])}/с")
            self.stat_rates[1].setText(f"↑ {size_text(data['upload_rate'])}/с")
            minutes, seconds = divmod(data["uptime"], 60)
            self.stat_rates[2].setText(f"Сеанс {minutes:02}:{seconds:02}")
            hours, remainder = divmod(data["uptime"], 3600)
            self.session_time.setText(f"{hours:02}:{remainder // 60:02}:{remainder % 60:02}")
            self.refresh_dashboard()
            self.set_groups(data["groups"])

        def failed(message):
            self.api_failures += 1
            if self.stack.currentIndex() == 8:
                self.flow_summary.setText(
                    "Не удалось обновить соединения. Показаны последние полученные данные."
                )
            if not self.core.running:
                self.set_disconnected()
            elif self.api_failures >= 3:
                self.state.setText("Нет связи с API ядра")
                self.navigation_status.setText("Нет связи")
                self.tray.setToolTip("Nodera · " + self.state.text())
                self.stats[2].setText("—")
            for value in self.stat_rates:
                value.setText("—")
            self.hint.setText(message)
            self.update_network_tag()

        def snapshot():
            try:
                data = self.core.status()
                data["groups"] = self.core.groups()
                return data
            except Exception:
                if not self.core.running:
                    self.core.stop("process exited during poll")
                raise

        generation = self.session_generation
        task = Task(snapshot, self)
        self.poll_task = task

        def success(data):
            if not self.closing and generation == self.session_generation:
                updated(data)
                if self.settings.geo_auto:
                    self.check_locations(automatic=True)
                import time

                if (
                    self.network_state != "unverified"
                    and time.monotonic() - self.network_checked_at >= 60
                ):
                    self.check_connection()

        def failure(message):
            if not self.closing and generation == self.session_generation:
                failed(message)

        def finished():
            if self.poll_task is task:
                self.poll_task = None
            task.deleteLater()
            if self.closing:
                QTimer.singleShot(0, self.close)

        task.result.connect(success)
        task.failed.connect(failure)
        task.finished.connect(finished)
        task.start()

    def start_inspection(self, key, callback, success, failure=None, session_bound=True):
        if self.closing or key in self.inspections:
            return
        generation = self.session_generation
        task = Task(callback, self)
        self.inspections[key] = task

        def result(value):
            if not self.closing and (not session_bound or generation == self.session_generation):
                success(value)

        task.result.connect(result)

        def failed(message):
            if not self.closing and (not session_bound or generation == self.session_generation):
                (failure or (lambda value: self.log(value, "warning", "Worker")))(message)

        task.failed.connect(failed)
        task.debug.connect(lambda message: self.log(message, "debug", "Worker"))

        def finished():
            self.inspections.pop(key, None)
            task.deleteLater()
            if self.closing:
                QTimer.singleShot(0, self.close)
            elif (
                key == "connectivity"
                and generation != self.session_generation
                and self.connected
                and self.network_checks_enabled
                and self.task is None
            ):
                QTimer.singleShot(0, self.check_connection)

        task.finished.connect(finished)
        task.start()

    def exit_app(self):
        self.quit_requested = True
        self.close()

    def closeEvent(self, event):
        self.navigation.palette.hide()
        if (
            self.settings.close_to_tray
            and not self.quit_requested
            and not self.closing
            and self.tray.isVisible()
        ):
            event.ignore()
            self.hide()
            return
        self.closing = True
        if self.channel_cancel:
            self.channel_cancel.set()
        self.shutdown_event.set()
        self.core.cancel_start()
        for timer in self.findChildren(QTimer):
            timer.stop()
        self.cancel_geo()
        if self.poll_task or self.inspections or self.geo_task:
            event.ignore()
            return
        if self.task:
            event.ignore()
            self.state.setText("Завершение…")
            return
        if self.core.process or self.core.runtime or self.core.system_proxy.pending:
            event.ignore()
            self.run_task(
                lambda: self.core.stop("window closing"), lambda _: self.set_disconnected()
            )
            return
        self.tray.hide()
        QApplication.instance().removeEventFilter(self)
        event.accept()
        if not self.shutdown_emitted:
            self.shutdown_emitted = True
            self.shutdown_complete.emit()
