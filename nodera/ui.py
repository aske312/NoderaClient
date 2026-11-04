import os
import time
import traceback
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QEasingCurve,
    QSignalBlocker,
    QSize,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLayout,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
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
from nodera.models import ClientError
from nodera.presentation import PresentationMixin
from nodera.profile_view import effective_tun
from nodera.profile_view import format_bytes as size_text
from nodera.profiles_ui import ProfilesMixin
from nodera.routes import route_for_node
from nodera.server_assets import node_countries
from nodera.theme import STYLE as STYLE
from nodera.versioning import version_text
from nodera.widgets import app_icon, label, line_icon
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
    def __init__(self, store, settings, profile, library=None):
        super().__init__()
        self.store, self.settings, self.profile = store, settings, profile
        self.library = library if library is not None else store.load_library()
        if profile:
            self.library = self.library.upsert(profile)
        self.profile_retry_at = {}
        self.pending_connect = False
        self.core = Core(store.root / "runtime")
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
        self.geo_results = {}
        self.delay_results = {}
        self.geo_client = GeoClient(store.root)
        self.journal = Journal(store.root, settings.log_limit, settings.save_logs)
        self.session_generation = 0
        self.quit_requested = False
        self.connected = False
        self.closing = False
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
        self.resize(940, 660)
        self.setMinimumSize(840, 620)
        root = QWidget()
        self.configure_shell(root)
        horizontal = QHBoxLayout(root)
        horizontal.setContentsMargins(0, 0, 0, 0)
        horizontal.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar_collapsed = settings.sidebar_collapsed
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(4, 8, 4, 8)
        side.setSpacing(4)
        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setObjectName("menuToggle")
        self.sidebar_toggle.setIconSize(QSize(18, 18))
        self.sidebar_toggle.setFixedSize(28, 28)
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        self.stack = QStackedWidget()
        self.nav = {}
        self.nav_titles = {}
        for index, text in (
            (0, "Главная"),
            (4, "Серверы"),
            (1, "Подписки"),
            (6, "Правила"),
            (8, "Соединения"),
            (2, "Настройки"),
            (7, "Приложение"),
        ):
            if index == 2:
                side.addSpacing(8)
            button = QToolButton()
            button.setText(text)
            button.setObjectName("nav")
            button.setFixedHeight(36)
            button.setIconSize(QSize(16, 16))
            button.setToolTip(text)
            button.setAccessibleName(text)
            button.setIcon(
                line_icon(
                    {
                        0: "home",
                        1: "profile",
                        2: "settings",
                        3: "events",
                        4: "servers",
                        5: "diagnostics",
                        6: "routes",
                        7: "info",
                        8: "connections",
                    }[index]
                )
            )
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.navigate(i))
            self.nav[index] = button
            self.nav_titles[index] = text
            side.addWidget(button)
        side.addStretch()
        self.sidebar_status = label("Не подключено", "muted")
        self.sidebar_status.hide()
        self.sidebar_version = label(version_text(), "railDetail", True)
        self.sidebar_version.setToolTip(version_text())
        side.addWidget(self.sidebar_version)
        side.addWidget(self.sidebar_toggle, 0, Qt.AlignmentFlag.AlignRight)
        horizontal.setContentsMargins(12, 0, 12, 12)
        horizontal.setSpacing(12)
        horizontal.addWidget(self.stack, 1)
        horizontal.addWidget(self.sidebar)
        self.sidebar_animation = QVariantAnimation(self)
        self.sidebar_animation.setDuration(180)
        self.sidebar_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.sidebar_animation.valueChanged.connect(
            lambda width: self.sidebar.setFixedWidth(int(width))
        )
        self.sidebar_animation.finished.connect(self.finish_sidebar_animation)
        self.apply_sidebar_state()
        self.sidebar_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        self.sidebar_shortcut.activated.connect(self.toggle_sidebar)
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
        self.delay_timer.setInterval(60000)
        self.delay_timer.timeout.connect(self.periodic_delays)
        self.delay_timer.start()
        self.log_timer = QTimer(self)
        self.log_timer.setInterval(500)
        self.log_timer.timeout.connect(self.ingest_core_logs)
        self.log_timer.start()
        self.subscription_timer = QTimer(self)
        self.subscription_timer.setInterval(60000)
        self.subscription_timer.timeout.connect(self.auto_update_profile)
        self.subscription_timer.start()
        QTimer.singleShot(1000, self.auto_update_profile)
        self.client_location_timer = QTimer(self)
        self.client_location_timer.setSingleShot(True)
        self.client_location_timer.timeout.connect(self.auto_client_location)
        self.client_location_timer.start(1500)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
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

    def apply_sidebar_state(self):
        self.sidebar.setFixedWidth(42 if self.sidebar_collapsed else 144)
        self.sidebar.setProperty("collapsed", self.sidebar_collapsed)
        self.sidebar.style().unpolish(self.sidebar)
        self.sidebar.style().polish(self.sidebar)
        self.sidebar_toggle.setIcon(line_icon("expand" if self.sidebar_collapsed else "collapse"))
        self.sidebar_toggle.setText("")
        self.sidebar_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        action = "Развернуть меню" if self.sidebar_collapsed else "Свернуть меню"
        self.sidebar_toggle.setAccessibleName(action)
        self.sidebar_toggle.setToolTip(action + " · Ctrl+B")
        for index, button in self.nav.items():
            button.setText("" if self.sidebar_collapsed else self.nav_titles[index])
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonIconOnly
                if self.sidebar_collapsed
                else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            )
            button.setFixedSize(
                34 if self.sidebar_collapsed else 128, 34 if self.sidebar_collapsed else 36
            )
            button.setProperty("circular", self.sidebar_collapsed)
            button.style().unpolish(button)
            button.style().polish(button)
        self.sidebar_status.hide()
        self.sidebar_version.setVisible(not self.sidebar_collapsed)

    def toggle_sidebar(self):
        if self.closing:
            return
        self.sidebar_animation.stop()
        start = self.sidebar.width()
        self.sidebar_collapsed = not self.sidebar_collapsed
        # Hide labels during the transition so they never get cut in half.
        for button in self.nav.values():
            button.setText("")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setFixedWidth(34)
        self.sidebar_toggle.setText("")
        self.sidebar_status.hide()
        self.sidebar_version.hide()
        self.sidebar_animation.setStartValue(start)
        self.sidebar_animation.setEndValue(42 if self.sidebar_collapsed else 144)
        self.sidebar_animation.start()
        settings = replace(self.settings, sidebar_collapsed=self.sidebar_collapsed)
        try:
            self.store.save_settings(settings)
            self.settings = settings
        except OSError:
            self.log("Не удалось сохранить вид меню.")

    def finish_sidebar_animation(self):
        self.apply_sidebar_state()

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
        if index in (3, 5):
            self.settings_tabs.setCurrentWidget(
                self.events_section if index == 3 else self.diagnostics_section
            )
            index = 2
        self.stack.setCurrentIndex(index)
        for i, button in self.nav.items():
            button.setChecked(i == index)
        if hasattr(self, "timer"):
            self.timer.setInterval(
                (self.settings.flow_poll_seconds if index == 8 else self.settings.poll_seconds)
                * 1000
            )
        if index == 8:
            self.render_flows(force=True)
            if hasattr(self, "timer") and self.connected:
                self.poll()

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
        self.task.failed.connect(failure or self.error)
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
            self.pending_connect = False
            QTimer.singleShot(0, self.toggle)
        elif (
            self.connected
            and self.network_checks_enabled
            and "connectivity" not in self.inspections
        ):
            QTimer.singleShot(0, self.check_connection)

    def refresh_controls(self):
        if self.profile:
            self.library = self.library.upsert(self.profile)
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
        self.update_button.setEnabled(
            self.task is None and not self.closing and self.profile is not None
        )
        self.import_button.setEnabled(self.task is None and not self.closing)
        self.profile_add.setEnabled(self.task is None and not self.closing)
        self.dashboard_update_button.setEnabled(
            self.task is None and not self.closing and self.profile is not None
        )
        self.delete_button.setEnabled(idle and self.profile is not None)
        self.rename_profile_button.setEnabled(
            self.task is None and not self.closing and self.profile is not None
        )
        self.profile_list.setEnabled(self.task is None and not self.closing)
        self.profile_selector.setEnabled(self.task is None and not self.closing)
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

        if not self.settings.client_point and not self.settings.client_location_attempted:
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
            usage = profile.usage
            text = f"Использовано: {size_text(usage.get('upload', 0) + usage.get('download', 0))}"
            if usage.get("total"):
                text += f" из {size_text(usage['total'])}"
            if usage.get("expire"):
                try:
                    text += f" · До {datetime.fromtimestamp(usage['expire']):%d.%m.%Y}"
                except (ValueError, OSError, OverflowError):
                    pass
            self.subscription_info.setText(
                f"Обновлён: {date}\n{text}" if usage else f"Обновлён: {date}"
            )
            self.hint.setText("")
        else:
            self.profile_title.setText("Нет профиля")
            self.profile_details.setText("Добавьте подписку")
            self.subscription_info.setText("Подписка ещё не загружена.")
            self.hint.setText("")
        self.refresh_controls()
        self.refresh_policy()
        self.refresh_dashboard()
        if not self.connected:
            self.offline_groups()

    def offline_groups(self):
        groups = []
        if self.profile:
            nodes = {node["name"]: node.get("type", "—") for node in self.profile.config["proxies"]}
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

    def current_group(self):
        name = self.group_combo.currentData()
        return next((group for group in self.groups_data if group["name"] == name), None)

    def current_node(self):
        item = self.node_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item and not item.isHidden() else None

    def render_nodes(self, *_):
        previous = self.current_node()
        blocker = QSignalBlocker(self.node_list)
        scroll = self.node_list.verticalScrollBar().value()
        group = self.current_group()
        if not group:
            self.group_hint.setText("Нет маршрутов")
        else:
            manual = group["type"] == "Selector"
            text = (
                "Ручной выбор"
                if manual
                else {
                    "Fallback": "Резервирование",
                    "URLTest": "Лучший отклик",
                    "LoadBalance": "Балансировка",
                    "Overview": "Маршруты профиля",
                }.get(group["type"], "Автоматически")
            )
            if not self.connected:
                text += " · Для следующего подключения"
            elif group["current"]:
                text += f" · Сейчас: {group['current']}"
            if self.profile and self.profile.forced_server:
                text = "Группа: " + group["name"] + " · Принудительный режим"
            self.group_hint.setText(text)
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
                active = (
                    " · используется" if self.connected and node["name"] == group["current"] else ""
                )
                item = existing.get(node["name"])
                if item is None:
                    item = QListWidgetItem()
                    self.node_list.insertItem(position, item)
                elif self.node_list.row(item) != position:
                    self.node_list.takeItem(self.node_list.row(item))
                    self.node_list.insertItem(position, item)
                geo = self.geo_for_profile().get(node["name"], {})
                address = next((row for row in geo.get("rows", []) if row.get("ip")), {})
                place = address.get("country", "")
                geo_text = " ".join(
                    filter(None, [address.get("ip", ""), place, address.get("city", "")])
                )
                definition = next(
                    (
                        entry
                        for entry in self.display_profile().config.get("proxies", [])
                        if entry["name"] == node["name"]
                    ),
                    {},
                )
                if not geo_text and definition.get("server"):
                    geo_text = str(definition["server"]) + (
                        ":" + str(definition["port"]) if definition.get("port") else ""
                    )
                codes = node_countries(
                    self.display_profile(), node["name"], geo, self.current_choices()
                )
                text = f"{node['name']}\n{node['type']} ? {delay}{active}\n{geo_text}"
                if item.text() != text:
                    item.setText(text)
                item.setData(Qt.ItemDataRole.UserRole, node["name"])
                item.setData(
                    Qt.ItemDataRole.UserRole + 1,
                    {
                        **node,
                        "active": self.connected
                        and (
                            node["name"] == self.profile.forced_server
                            if self.profile and self.profile.forced_server
                            else node["name"] in group.get("active", [group["current"]])
                        ),
                        "forced": bool(self.profile and self.profile.forced_server == node["name"]),
                        "frozen": node["name"] in self.core.frozen_routes,
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
                        + "\nЗаморожен после двух ошибок. Возврат — не раньше 2 минут и после успешной проверки."
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
        self.route_mode_label.setText(
            ("Закреплён маршрут: " + forced + ("" if self.connected else " · Для подключения"))
            if forced
            else "Стратегия подписки · " + self.profile_policy.get("strategy", "Не задана")
        )
        self.force_button.setVisible(True)
        self.force_button.setChecked(bool(forced))
        self.force_button.setText("Вернуть стратегию" if forced else "Включить маршрут")
        self.route_actions.setVisible(self.server_tabs.currentIndex() == 0)
        group = self.current_group()
        concrete = bool(
            self.profile
            and any(
                node.get("name") == self.current_node()
                for node in self.profile.config.get("proxies", [])
            )
        )
        frozen = self.current_node() in self.core.frozen_routes
        self.force_button.setEnabled(
            bool(
                idle
                and (forced or concrete and not frozen and self.server_tabs.currentIndex() == 0)
            )
        )
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
        self.geo_button.setEnabled(bool(idle and self.profile and not self.geo_task))
        self.refresh_servers_button.setEnabled(idle and self.connected)
        self.location_button.setEnabled(
            bool(
                idle
                and self.profile
                and self.current_node()
                and any(
                    node["name"] == self.current_node()
                    for node in self.profile.config.get("proxies", [])
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
        dialog = RouteDialog(hops, self)
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

    def force_server(self):
        member = self.current_node()
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
                self.pending_connect = True
            self.server_result.setText(
                "Принудительно: " + member + ("" if saved else " · Не сохранено")
            )
            self.log("Включён принудительный маршрут: " + member)
            self.refresh_dashboard()

        self.run_task(apply, done)

    def toggle_forced_route(self):
        self.force_button.setChecked(bool(self.profile and self.profile.forced_server))
        if self.profile and self.profile.forced_server:
            self.restore_server_policy()
        else:
            self.force_server()

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
            if self.settings.tun and not is_admin():
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
        state = self.network_state if self.connected else "off"
        text = {
            "off": "Отключено",
            "unverified": "Сеть не проверена",
            "checking": "Проверяем сеть…",
            "ok": "Сеть доступна",
            "error": "Сбой проверки сети",
        }[state]
        self.network_tag.setText(text)
        self.network_tag.setProperty("state", state)
        self.network_tag.style().unpolish(self.network_tag)
        self.network_tag.style().polish(self.network_tag)
        self.network_tag.setEnabled(self.connected and state != "checking" and not self.closing)
        stamp = (
            datetime.fromtimestamp(self.network_checked_wall).strftime("%H:%M:%S")
            if self.network_checked_wall
            else "ещё не выполнена"
        )
        self.network_tag.setToolTip(
            "HTTPS через Nodera · Проверка: " + stamp + ". Нажмите для повторной проверки."
        )

    def check_connection(self):
        if not self.connected or self.closing or "connectivity" in self.inspections:
            return
        import time

        self.network_checked_at = time.monotonic()
        self.network_state = "checking"
        self.state.setText(self.connection_state_text())
        self.sidebar_status.setText("Проверка сети…")
        self.tray.setToolTip("Nodera · " + self.state.text())

        def checked(_):
            self.network_checked_wall = time.time()
            self.network_state = "ok"
            self.network_error = ""
            self.state.setText(self.connection_state_text())
            self.sidebar_status.setText("HTTPS проверен")
            self.tray.setToolTip("Nodera · " + self.state.text())
            self.hint.setText(self.connection_hint)
            self.log(
                "HTTPS-запрос через локальный прокси прошёл. Перехват TUN проверяется отдельно."
            )

        def failed(message):
            self.network_checked_wall = time.time()
            self.network_state = "error"
            self.network_error = message
            self.state.setText(self.connection_state_text())
            self.sidebar_status.setText("Проверка не прошла")
            self.tray.setToolTip("Nodera · " + self.state.text())
            self.hint.setText(message)
            self.log(message, "warning")
            self.recover_routes()
            self.test_group()

        self.start_inspection("connectivity", self.core.check_connectivity, checked, failed)

    def recover_routes(self):
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
        self.session_tun = effective_tun(self.profile.config if self.profile else {}, self.settings)
        self.api_failures = 0
        self.network_state = "unverified"
        self.network_error = ""
        self.network_checked_wall = 0.0
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
        self.sidebar_status.setText(self.state.text())
        self.update_route_pill()
        if verify:
            self.check_connection()
            QTimer.singleShot(0, self.periodic_delays)

    def set_disconnected(self):
        self.delay_results.clear()
        self.delay_status.setText("Все маршруты · Автопроверка после подключения, каждые 60 с")
        self.flow_monitor.clear()
        self.connected = False
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
        self.sidebar_status.setText("Не подключено")
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
        self.hint.setText(message)
        self.error(message)

    def poll(self):
        if not self.connected or self.task or self.poll_task or self.closing:
            return

        def updated(data):
            if self.api_failures:
                self.hint.setText(self.network_error or self.connection_hint)
            self.api_failures = 0
            self.state.setText(self.connection_state_text())
            self.sidebar_status.setText(
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
                self.sidebar_status.setText("Нет связи")
                self.tray.setToolTip("Nodera · " + self.state.text())
                self.stats[2].setText("—")
            for value in self.stat_rates:
                value.setText("—")
            self.hint.setText(message)

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
        self.timer.stop()
        self.subscription_timer.stop()
        self.log_timer.stop()
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
        event.accept()
