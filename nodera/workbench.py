"""Operational pages: small toolbars, stable lists and explicit background checks."""

import json
import time
from dataclasses import replace
from datetime import datetime

import httpx
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nodera import __version__, updater
from nodera.bundled_core import core_manifest
from nodera.controller import Controller
from nodera.controls import ComboBox as QComboBox
from nodera.delay_probe import probe_delays
from nodera.device_info import windows_identity
from nodera.diagnostics import preflight
from nodera.geolocation import endpoint_rows
from nodera.journal import compact_entries, format_entry, profile_secrets, useful_event
from nodera.locations import country
from nodera.models import ClientError
from nodera.route_inventory import route_inventory
from nodera.rule_editor import RuleDialog
from nodera.server_assets import flag_pixmap
from nodera.versioning import build_info, version_text
from nodera.widgets import NodeList, app_icon, label
from nodera.workers import GeoTask


def combo(values, current):
    widget = QComboBox()
    for text, value in values:
        widget.addItem(text, value)
    widget.setCurrentIndex(max(0, widget.findData(current)))
    return widget


class WorkbenchMixin:
    def embed_settings_tools(self):
        for index, attribute in (
            (3, "events_section"),
            (5, "diagnostics_section"),
        ):
            section = self.stack.widget(index)
            self.stack.removeWidget(section)
            self.stack.insertWidget(index, QWidget())
            inner = section.widget()
            inner.layout().setContentsMargins(4, 6, 4, 4)
            for heading in inner.findChildren(QLabel, "title"):
                heading.hide()
            setattr(self, attribute, section)
        self.settings_tabs.addTab(self.diagnostics_section, "Диагностика")
        self.settings_tabs.addTab(self.events_section, "Журнал")
        self.settings_tabs.currentChanged.connect(self.settings_section_changed)

    def settings_section_changed(self, index):
        editing = self.settings_tabs.currentWidget() not in {
            self.diagnostics_section,
            self.events_section,
        }
        self.save_button.setVisible(editing)
        self.settings_result.setVisible(editing)
        if (
            self.settings_tabs.currentWidget() is self.diagnostics_section
            and not self.diagnostic_list.count()
        ):
            self.diagnose()

    def build_application(self):
        layout = self.page("Приложение")
        row = QHBoxLayout()
        icon = label("")
        icon.setPixmap(app_icon().pixmap(48, 48))
        row.addWidget(icon)
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(label("Nodera", "dashboardProfile"))
        column.addWidget(label("VPN-клиент для Windows · Proxy и TUN", "muted"))
        row.addLayout(column, 1)
        layout.addLayout(row)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        os_name, os_release = windows_identity()
        self.application_values = {
            "Версия приложения": __version__,
            "Сборка": build_info()["build"],
            "Ядро": "Mihomo " + core_manifest()["version"].removeprefix("v"),
            "Система": os_name + " · " + os_release,
            "Режимы": "Proxy (HTTP / SOCKS5), TUN (VPN)",
            "Подписки": "Mihomo YAML · автоматическое и ручное обновление",
            "Хранение": "Локально · Windows DPAPI",
        }
        panel = QWidget()
        panel.setObjectName("applicationCard")
        panel.setMaximumWidth(510)
        panel.setMinimumWidth(420)
        self.application_panel = panel
        content = QVBoxLayout(panel)
        content.setContentsMargins(22, 20, 22, 20)
        content.setSpacing(16)
        layout.removeItem(row)
        content.addLayout(row)
        self.application_version = label(
            version_text(),
            "profileName",
        )
        self.application_version.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        content.addWidget(self.application_version)
        for caption, text in self.application_values.items():
            if caption in {"Версия приложения", "Сборка", "Подписки"}:
                continue
            value = label(text, wrap=True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(caption, value)
        content.addLayout(form)
        self.available_release = None
        self.app_update_status = label("Обновления из GitHub Releases", "muted", True)
        content.addWidget(self.app_update_status)
        updates = QHBoxLayout()
        self.app_update_check = QPushButton("Проверить обновления")
        self.app_update_check.clicked.connect(self.check_app_updates)
        self.app_update_install = QPushButton("Скачать и установить")
        self.app_update_install.hide()
        self.app_update_install.clicked.connect(self.install_app_update)
        updates.addWidget(self.app_update_check)
        updates.addWidget(self.app_update_install)
        content.addLayout(updates)
        layout.addStretch(1)
        layout.addWidget(panel, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(2)

    def update_proxy(self):
        return (
            f"http://127.0.0.1:{self.core.mixed_port}"
            if self.connected and self.core.mixed_port
            else None
        )

    def check_app_updates(self):
        if self.task or self.closing:
            return
        self.app_update_check.setEnabled(False)
        self.app_update_install.setEnabled(False)
        self.app_update_status.setText("Проверка GitHub…")
        proxy = self.update_proxy()

        def checked(release):
            self.available_release = release
            self.app_update_check.setEnabled(True)
            self.app_update_install.setEnabled(True)
            self.app_update_install.setVisible(release is not None)
            self.app_update_status.setText(
                f"Доступна версия {release.version}. Для установки Nodera завершит работу."
                if release
                else "Установлена актуальная версия"
            )

        def failed(message):
            self.app_update_check.setEnabled(True)
            self.app_update_install.setEnabled(self.available_release is not None)
            self.app_update_status.setText(message)

        self.start_inspection(
            "app-update",
            lambda: updater.check_release(proxy=proxy),
            checked,
            failed,
            session_bound=False,
        )

    def install_app_update(self):
        if self.task or self.closing or not self.available_release:
            return
        release, proxy = self.available_release, self.update_proxy()
        self.app_update_install.setEnabled(False)
        self.app_update_status.setText("Загрузка и проверка установщика…")

        def downloaded(path):
            try:
                updater.launch_installer(path, release)
            except ClientError as exc:
                failed(str(exc))
                return
            self.exit_app()

        def failed(message):
            self.app_update_install.setEnabled(True)
            self.app_update_status.setText(message)

        self.run_task(
            lambda: updater.download_release(release, self.store.root, proxy=proxy),
            downloaded,
            failed,
        )

    def build_servers(self):
        layout = self.page("Серверы")
        mode_row = QHBoxLayout()
        self.route_mode_label = label("Стратегия подписки", "routeMode", True)
        mode_row.addWidget(self.route_mode_label, 1)
        self.restore_policy_button = self.force_button = QPushButton("Включить маршрут")
        self.force_button.setObjectName("forceRoute")
        self.force_button.setCheckable(True)
        self.force_button.clicked.connect(self.toggle_forced_route)
        self.restore_policy_action = self.restore_policy_button
        mode_row.addWidget(self.restore_policy_button)
        layout.addLayout(mode_row)
        bar = QHBoxLayout()
        self.group_combo = QComboBox()
        self.group_combo.setAccessibleName("Группа серверов")
        self.group_combo.currentIndexChanged.connect(self.render_nodes)
        self.server_search = QLineEdit()
        self.server_search.setPlaceholderText("Найти маршрут, IP или страну")
        self.server_search.textChanged.connect(self.filter_nodes)
        self.server_sort = combo(
            [("Из подписки", "profile"), ("По имени", "name"), ("По задержке", "delay")], "profile"
        )
        self.server_sort.currentIndexChanged.connect(self.render_nodes)
        self.group_combo.setMaximumWidth(190)
        self.server_sort.setMaximumWidth(150)
        bar.addWidget(self.group_combo, 1)
        bar.addWidget(self.server_search, 2)
        bar.addWidget(self.server_sort)
        layout.addLayout(bar)
        self.group_hint = label("Нет маршрутов", "muted", True)
        layout.addWidget(self.group_hint)
        self.server_tabs = QTabWidget()
        self.node_list = NodeList()
        self.node_list.currentRowChanged.connect(self.server_selection_changed)
        self.server_tabs.addTab(self.node_list, "Маршруты")
        addresses = QWidget()
        column = QVBoxLayout(addresses)
        column.setContentsMargins(0, 10, 0, 0)
        self.address_table = QTableWidget(0, 7)
        self.address_table.setHorizontalHeaderLabels(
            ["Участок / адрес", "IP", "Страна", "Город", "Сеть / ASN", "Кол-во", "Проверено"]
        )
        self.address_table.verticalHeader().hide()
        self.address_table.horizontalHeaderItem(5).setToolTip("Количество маршрутов с этим адресом")
        self.address_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.address_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.address_table.horizontalHeader().setStretchLastSection(True)
        for index, width in enumerate((155, 110, 125, 70, 95, 50, 72)):
            self.address_table.setColumnWidth(index, width)
        self.address_table.setWordWrap(False)
        column.addWidget(self.address_table, 1)
        copy_addresses = QPushButton("Копировать выбранные адреса")
        copy_addresses.clicked.connect(self.copy_addresses)
        column.addWidget(copy_addresses, 0, Qt.AlignmentFlag.AlignLeft)
        shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.address_table)
        shortcut.activated.connect(self.copy_addresses)
        self.address_note = label("Выберите маршрут и нажмите «Проверить IP».", "muted", True)
        column.addWidget(self.address_note)
        self.server_tabs.addTab(addresses, "IP и маршрут")
        layout.addWidget(self.server_tabs, 1)
        self.select_button = QPushButton("Выбрать")
        self.select_button.setObjectName("primary")
        self.select_button.clicked.connect(self.select_server)
        self.force_button.setToolTip(
            "Все новые соединения через выбранный маршрут, независимо от правил и стратегии"
        )
        self.delay_button = QPushButton("Задержка")
        self.delay_button.setToolTip(
            "HTTPS-проверка всех маршрутов подписки. Автоматически — каждые 60 секунд при подключении."
        )
        self.delay_button.clicked.connect(self.test_group)
        self.location_button = QPushButton("Редактировать маршрут")
        self.location_button.clicked.connect(self.choose_location)
        self.geo_button = QPushButton("Проверить IP")
        self.geo_button.setToolTip("Проверить IP всех маршрутов подписки")
        self.geo_button.clicked.connect(lambda: self.check_locations(all_nodes=True))
        self.refresh_servers_button = QPushButton("Обновить")
        self.refresh_servers_button.clicked.connect(self.reload_servers)
        self.geo_all_button = self.geo_button
        self.delay_all_button = self.delay_button
        self.geo_cancel_button = QPushButton("Остановить")
        self.geo_cancel_button.clicked.connect(self.cancel_geo)
        self.geo_cancel_button.setVisible(False)
        checks = QHBoxLayout()
        for button in (self.delay_button, self.geo_button, self.geo_cancel_button):
            checks.addWidget(button)
        self.delay_status = label("Все маршруты · Задержка обновляется каждые 60 с", "muted", True)
        checks.addWidget(self.delay_status, 1)
        checks.addWidget(self.refresh_servers_button)
        layout.insertLayout(layout.indexOf(self.server_tabs), checks)
        self.route_actions = QWidget()
        row = QHBoxLayout(self.route_actions)
        row.setContentsMargins(0, 0, 0, 0)
        for button in (
            self.select_button,
            self.location_button,
        ):
            row.addWidget(button)
        row.addStretch()
        layout.addWidget(self.route_actions)
        self.server_tabs.currentChanged.connect(self.refresh_server_controls)
        self.server_result = label("", "muted", True)
        layout.addWidget(self.server_result)

    def copy_addresses(self):
        rows = sorted({item.row() for item in self.address_table.selectedItems()})
        if not rows:
            rows = range(self.address_table.rowCount())
        QApplication.clipboard().setText(
            "\n".join(
                "\t".join(
                    self.address_table.item(row, column).text()
                    for column in range(self.address_table.columnCount())
                )
                for row in rows
            )
        )

    def server_selection_changed(self, *_):
        self.refresh_server_controls()
        self.render_addresses()

    def geo_for_profile(self):
        if not self.profile:
            return {}
        result = {}
        for (profile_id, name), value in self.geo_results.items():
            if profile_id == self.profile.id and value.get("endpoints") == endpoint_rows(
                self.display_profile(), name, self.current_choices()
            ):
                result[name] = value
        return result

    def current_choices(self):
        choices = {group["name"]: group.get("current", "") for group in self.groups_data}
        if self.groups_data:
            choices.update(self.groups_data[0].get("choices", {}))
        return choices

    def render_addresses(self):
        if not hasattr(self, "address_table"):
            return
        results = self.geo_for_profile()
        key = (
            self.profile.id if self.profile else None,
            id(self.display_profile().config) if self.profile else None,
            repr(self.display_profile().route_overrides) if self.profile else None,
            tuple(sorted((name, value.get("timestamp")) for name, value in results.items())),
            tuple(sorted(self.current_choices().items())),
        )
        if getattr(self, "_address_key", None) == key:
            return
        self._address_key = key
        rows = route_inventory(
            self.display_profile(), results, self.current_choices(), self.geo_client.cache
        )
        self.address_table.setRowCount(len(rows))
        roles = {"entry": "Вход", "relay": "Relay", "cdn": "CDN", "server": "Выход"}
        for row, entry in enumerate(rows):
            codes = sorted(entry["countries"])
            places = [country(code) for code in codes]
            checked = (
                datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M:%S")
                if entry.get("timestamp")
                else "—"
            )
            values = [
                "/".join(roles.get(role, role) for role in sorted(entry["roles"]))
                + " · "
                + entry["host"],
                entry.get("ip") or "—",
                ", ".join(place["name"] for place in places if place) or "Не определена",
                entry.get("city", "—"),
                " / ".join(filter(None, [entry.get("org", ""), entry.get("asn", "")])),
                str(len(entry["nodes"])),
                checked,
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setToolTip(
                    str(text)
                    + "\n"
                    + entry.get("status", "")
                    + "\n"
                    + "\n".join(sorted(entry["nodes"]))
                )
                if column == 2:
                    item.setIcon(QIcon(flag_pixmap(codes[0] if codes else "xx")))
                self.address_table.setItem(row, column, item)
        checked = sum(bool(entry.get("timestamp")) for entry in rows)
        unknown = sum(entry["host"] == "Адрес не передан" for entry in rows)
        self.address_note.setText(
            f"Все маршруты профиля · Адресов: {len(rows) - unknown} · Скрытых групп: {unknown} · Проверено по IP: {checked}. "
            "Скрытые адреса сгруппированы по роли; их совпадение не подтверждено."
        )

    def check_locations(self, all_nodes=False, automatic=False):
        if self.closing or self.geo_task or not self.profile:
            return
        names = (
            [item["name"] for item in self.display_profile().config.get("proxies", [])]
            if all_nodes
            else [self.current_node()]
        )
        if automatic:
            names = list(
                dict.fromkeys(
                    name for group in self.groups_data for name in group.get("active", [])
                )
            )[:4]
        names = [
            name
            for name in names
            if name and endpoint_rows(self.display_profile(), name, self.current_choices())
        ]
        if automatic:
            names = [
                name
                for name in names
                if time.time() - self.geo_for_profile().get(name, {}).get("timestamp", 0) > 300
            ]
        if not names:
            if not automatic:
                self.server_result.setText(
                    "Адрес маршрута не передан. Укажите его в редакторе маршрута."
                )
            return
        profile, choices = self.display_profile(), self.current_choices()
        self.geo_client.proxy = (
            f"http://127.0.0.1:{self.core.mixed_port}"
            if self.connected and self.core.mixed_port
            else None
        )
        dns = None
        if self.connected:
            port, secret = self.core.port, self.core.secret

            def dns(host, kind):
                with Controller(port, secret) as controller:
                    return controller.request(
                        "GET", "/dns/query", params={"name": host, "type": kind}
                    )

        task = GeoTask(
            self.geo_client,
            profile,
            names,
            choices,
            dns,
            self,
            force=not automatic,
            core_pid=self.core.process.pid if self.core.process and self.core.running else None,
        )
        self.geo_task = task
        self.geo_cancel_button.setEnabled(True)
        self.geo_cancel_button.setVisible(True)
        self.geo_button.setEnabled(False)
        self.geo_all_button.setEnabled(False)
        self.server_result.setText("Проверка IP в фоне…")

        def checked(name, value):
            if self.closing:
                return
            value["endpoints"] = endpoint_rows(profile, name, choices)
            self.geo_results[(profile.id, name)] = value
            self.log(f"{name}: проверено адресов {len(value['rows'])}.", source="Геолокация")
            for row in value["rows"]:
                self.log(
                    f"{row['role']} · {row['host']} · {row.get('ip') or '—'} · "
                    f"{row.get('country') or '—'} · {row['status']}",
                    "info" if row.get("country") else "warning",
                    "Геолокация",
                )
            if self.profile and self.profile.id == profile.id:
                self.render_addresses()
                self.update_route_pill()
                self.render_nodes()

        task.progress.connect(checked)
        task.message.connect(lambda message: self.log(message, "warning", "Геолокация"))

        def finished():
            if self.geo_task is task:
                self.geo_task = None
            task.deleteLater()
            self.geo_cancel_button.setEnabled(False)
            self.geo_cancel_button.setVisible(False)
            self.geo_button.setEnabled(True)
            self.geo_all_button.setEnabled(True)
            self.server_result.setText(
                "Проверка IP завершена" if not task.cancelled.is_set() else "Проверка остановлена"
            )
            if self.closing:
                QTimer.singleShot(0, self.close)

        task.finished.connect(finished)
        task.start()

    def cancel_geo(self):
        if self.geo_task:
            self.geo_task.cancelled.set()

    def test_group(self):
        if (
            not self.connected
            or self.task
            or self.closing
            or not self.profile
            or "delays" in self.inspections
        ):
            return
        names = [item["name"] for item in self.display_profile().config.get("proxies", [])]
        if not names:
            return
        port, secret = self.core.port, self.core.secret
        profile_id = self.profile.id
        generation = self.session_generation
        self.delay_status.setText(f"Проверка всех маршрутов: {len(names)}…")

        def checked(values):
            self.delay_results.update({(profile_id, name): value for name, value in values.items()})
            for group in self.groups_data:
                for item in group["members"]:
                    if item["name"] in values:
                        item["delay"] = values[item["name"]]["value"]
            self.render_nodes()
            available = sum(value["value"] > 0 for value in values.values())
            self.delay_status.setText(
                f"{datetime.now():%H:%M:%S} · Доступно {available}/{len(values)} · Авто: 60 с"
            )

        def failed(message):
            self.delay_status.setText("Проверка задержек не завершена")
            self.log(message, "warning")

        def check():
            aliases = self.core.probe_aliases()

            def cancelled():
                return self.closing or generation != self.session_generation

            values = probe_delays(port, secret, names, cancelled, aliases)
            if not cancelled():
                self.core.update_route_health(values)
            return values

        self.start_inspection(
            "delays",
            check,
            checked,
            failed,
        )
        self.refresh_server_controls()
        self.inspections["delays"].finished.connect(self.refresh_server_controls)

    def periodic_delays(self):
        if self.connected and self.core.running:
            self.test_group()

    def build_settings(self):
        from nodera.settings_ui import build_settings

        build_settings(self)

    def save_settings(self):
        if self.task or self.closing:
            return
        from nodera.settings_ui import client_point_value

        settings = replace(
            self.settings,
            mixed_port=self.port.value() if self.port.value() >= 1024 else 0,
            tun=self.connection_mode.currentData() != "proxy",
            system_proxy=self.system_proxy_check.isChecked(),
            update_hours=self.update_interval.currentData(),
            poll_seconds=self.poll_interval.currentData(),
            log_level=self.log_level_setting.currentData(),
            log_limit=self.log_limit_setting.value(),
            save_logs=self.log_save_check.isChecked(),
            geo_auto=self.geo_auto_check.isChecked(),
            tun_stack=self.stack_setting.currentData(),
            tun_mtu=self.mtu_setting.value() if self.mtu_setting.value() >= 1280 else 0,
            ipv6_mode=self.ipv6_setting.currentData(),
            close_to_tray=self.close_tray_check.isChecked(),
            flow_poll_seconds=self.flow_poll_setting.currentData(),
            flow_history_minutes=self.flow_history_setting.currentData(),
            save_connection_logs=self.connection_log_check.isChecked(),
            connection_log_mb=self.connection_log_size.currentData(),
            connection_log_interval=self.connection_log_interval_setting.currentData(),
            map_auto_zoom=self.map_auto_zoom_check.isChecked(),
            client_point=client_point_value(self),
        )
        try:
            self.store.save_settings(settings)
        except OSError:
            self.error("Не удалось сохранить настройки.")
            return
        self.settings = settings
        self.port.setValue(settings.mixed_port)
        self.mtu_setting.setValue(settings.tun_mtu)
        self.timer.setInterval(
            (
                settings.flow_poll_seconds
                if self.stack.currentIndex() == 8
                else settings.poll_seconds
            )
            * 1000
        )
        self.flow_monitor.retention_seconds = settings.flow_history_minutes * 60
        self.journal.configure(settings.log_limit, settings.save_logs)
        self.core.trace.configure(settings.log_limit, settings.save_logs)
        self.core.connection_log.configure(
            settings.save_connection_logs,
            settings.connection_log_mb,
            settings.connection_log_interval,
        )
        self.render_flows(force=True)
        self.events.document().setMaximumBlockCount(settings.log_limit)
        self.settings_result.setText("Сохранено. Сеть — при следующем подключении.")
        self.log("Настройки сохранены.")
        self.refresh_dashboard()
        if self.connected:
            self.run_task(
                lambda: self.core.set_log_level(settings.log_level),
                lambda _: self.settings_result.setText("Сохранено. Уровень логов применён."),
            )

    def build_events(self):
        layout = self.page("Журнал")
        row = QHBoxLayout()
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("Поиск в журнале")
        self.log_filter = combo(
            [
                ("Все уровни", ""),
                ("Debug", "debug"),
                ("Info", "info"),
                ("Warning", "warning"),
                ("Error", "error"),
            ],
            "",
        )
        self.log_source = combo(
            [
                (text, value)
                for text, value in (
                    ("Все источники", ""),
                    ("Nodera", "Nodera"),
                    ("Mihomo", "Mihomo"),
                    ("Ядро", "Ядро"),
                    ("Соединения", "Соединения"),
                    ("Подписки", "Подписки"),
                    ("Геолокация", "Геолокация"),
                    ("Диагностика", "Диагностика"),
                )
            ],
            "",
        )
        row.addWidget(self.log_search, 2)
        row.addWidget(self.log_filter)
        row.addWidget(self.log_source)
        layout.addLayout(row)
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.events.document().setMaximumBlockCount(self.settings.log_limit)
        layout.addWidget(self.events, 1)
        row = QHBoxLayout()
        self.log_pause = QCheckBox("Пауза")
        self.log_follow = QCheckBox("Автопрокрутка")
        self.log_follow.setChecked(True)
        self.log_detailed = QCheckBox("Подробно")
        self.log_detailed.setToolTip(
            "Показать отладочные события. Подробности соединений сохраняются в network.jsonl."
        )
        row.addWidget(self.log_pause)
        row.addWidget(self.log_follow)
        row.addWidget(self.log_detailed)
        row.addStretch()
        export = QPushButton("Экспорт")
        export.clicked.connect(self.export_logs)
        clear = QPushButton("Очистить")
        clear.clicked.connect(self.clear_logs)
        row.addWidget(export)
        row.addWidget(clear)
        layout.addLayout(row)
        self.log_count = label("", "muted")
        layout.addWidget(self.log_count)
        self.log_search.textChanged.connect(self.render_events)
        self.log_filter.currentIndexChanged.connect(self.render_events)
        self.log_source.currentIndexChanged.connect(self.render_events)
        self.log_pause.toggled.connect(lambda paused: None if paused else self.render_events())
        self.log_detailed.toggled.connect(self.render_events)
        self.render_events()

    def matching_logs(self):
        query = self.log_search.text().casefold()
        level = self.log_filter.currentData()
        source = self.log_source.currentData()
        entries = [
            entry
            for entry in self.journal.entries
            if (not level or entry["level"] == level)
            and (not source or entry["source"] == source)
            and query in format_entry(entry).casefold()
            and (self.log_detailed.isChecked() or useful_event(entry))
        ]
        return entries if self.log_detailed.isChecked() else compact_entries(entries)

    def render_events(self, *_):
        if not hasattr(self, "events"):
            return
        values = self.matching_logs()
        scroll = self.events.verticalScrollBar().value()
        self.events.setPlainText("\n".join(format_entry(entry) for entry in values))
        self.events.verticalScrollBar().setValue(
            self.events.verticalScrollBar().maximum() if self.log_follow.isChecked() else scroll
        )
        self.log_count.setText(f"Показано {len(values)} из {len(self.journal.entries)}")

    def log(self, message, level="info", source="Nodera"):
        key = (
            tuple(
                (profile.id, profile.url, id(profile.config)) for profile in self.library.profiles
            ),
            self.core.secret,
        )
        if getattr(self, "_log_secrets_key", None) != key:
            self._log_secrets_key = key
            self._log_secrets = [
                secret for profile in self.library.profiles for secret in profile_secrets(profile)
            ]
            if self.core.secret:
                self._log_secrets.append(self.core.secret)
        secrets = self._log_secrets
        entry = self.journal.add(message, level, source, secrets)
        if not hasattr(self, "events") or self.log_pause.isChecked():
            return
        if not self.log_detailed.isChecked():
            if useful_event(entry):
                self.render_events()
            return
        if (
            (self.log_filter.currentData() and entry["level"] != self.log_filter.currentData())
            or (self.log_source.currentData() and entry["source"] != self.log_source.currentData())
            or self.log_search.text().casefold() not in format_entry(entry).casefold()
        ):
            return
        bar = self.events.verticalScrollBar()
        position = bar.value()
        at_end = position >= bar.maximum() - 2
        self.events.appendPlainText(format_entry(entry))
        if not self.log_follow.isChecked() or not at_end:
            bar.setValue(position)
        self.log_count.setText(f"Записей: {len(self.journal.entries)}")

    def ingest_core_logs(self):
        for level, message in self.core.drain_logs(limit=200):
            source = (
                "Подписки"
                if message.startswith("[Subscription]")
                else "Соединения"
                if message.startswith("[Connections]")
                else "Ядро"
                if message.startswith("[Core]")
                else "Mihomo"
            )
            if source == "Подписки" and getattr(self, "subscription_state", None) == "loading":
                for event, caption in (
                    ("subscription_response", "Получение данных…"),
                    ("subscription_body_received", "Проверка подписки…"),
                    ("subscription_save_started", "Сохранение подписки…"),
                ):
                    if f"event={event} " in message or message.endswith(f"event={event}"):
                        self.subscription_status.setText(caption)
            if source == "Соединения" and level not in {"warning", "error"}:
                continue  # Already retained by Core.trace in network.jsonl.
            self.log(message, level, source)

    def export_logs(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт журнала", "nodera-log.txt", "Текст (*.txt);;JSON (*.json)"
        )
        if not path:
            return
        from pathlib import Path

        values = self.matching_logs()
        try:
            Path(path).write_text(
                json.dumps(values, ensure_ascii=False, indent=2)
                if path.endswith(".json")
                else "\n".join(format_entry(entry) for entry in values),
                encoding="utf-8",
            )
        except OSError:
            self.error("Не удалось записать журнал.")

    def clear_logs(self):
        try:
            self.journal.clear()
            self.render_events()
        except OSError:
            self.error("Не удалось очистить файлы журнала.")

    def build_diagnostics(self):
        layout = self.page("Диагностика")
        row = QHBoxLayout()
        self.diagnostic_button = QPushButton("Проверить локально")
        self.diagnostic_button.setObjectName("primary")
        self.diagnostic_button.clicked.connect(self.diagnose)
        self.network_diagnostic_button = QPushButton("Проверить сеть и выходной IP")
        self.network_diagnostic_button.clicked.connect(self.diagnose_network)
        copy = QPushButton("Копировать отчёт")
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(
                "\n\n".join(
                    self.diagnostic_list.item(i).text() for i in range(self.diagnostic_list.count())
                )
            )
        )
        row.addWidget(self.diagnostic_button)
        row.addWidget(self.network_diagnostic_button)
        row.addWidget(copy)
        layout.addLayout(row)
        self.diagnostic_list = QListWidget()
        self.diagnostic_list.setWordWrap(True)
        layout.addWidget(self.diagnostic_list, 1)
        layout.addWidget(
            label(
                "Проверки выполняются в фоне. IP выхода относится к проверочному запросу и зависит от правил.",
                "muted",
                True,
            )
        )

    def show_checks(self, checks):
        self.diagnostic_list.clear()
        for check in checks:
            prefix = {"ok": "ГОТОВО", "error": "ОШИБКА", "info": "ИНФОРМАЦИЯ"}[check["state"]]
            self.diagnostic_list.addItem(
                QListWidgetItem(f"{prefix} · {check['title']}\n{check['detail']}")
            )
            self.log(
                f"{check['title']}: {check['detail']}",
                "error" if check["state"] == "error" else "info",
                "Диагностика",
            )
        self.log(f"Проверок завершено: {len(checks)}.", source="Диагностика")

    def diagnose(self):
        if self.closing:
            return
        settings, profile, running = self.settings, self.profile, self.core.running
        self.start_inspection(
            "local", lambda: preflight(settings, profile, running), self.show_checks
        )

    def diagnose_network(self):
        if not self.connected:
            self.show_checks(
                [
                    {
                        "title": "Ядро",
                        "state": "info",
                        "detail": "Подключитесь для проверки внешнего IP через локальный прокси.",
                    }
                ]
            )
            return
        port, secret, mixed = self.core.port, self.core.secret, self.core.mixed_port

        def check():
            checks = []
            with Controller(port, secret) as controller:
                version = controller.request("GET", "/version")
                config = controller.request("GET", "/configs")
            checks.append(
                {
                    "title": "API ядра",
                    "state": "ok",
                    "detail": f"Mihomo {version.get('version', '—')}; авторизованный ответ получен.",
                }
            )
            checks.append(
                {
                    "title": "Текущий сеанс",
                    "state": "ok",
                    "detail": f"Прокси 127.0.0.1:{mixed} · режим {config.get('mode', '—')} · IPv6 {config.get('ipv6', '—')} · логи {config.get('log-level', '—')}",
                }
            )
            try:
                with Controller(port, secret) as controller:
                    dns = controller.request(
                        "GET", "/dns/query", params={"name": "example.com", "type": "A"}
                    )
                addresses = [
                    str(item.get("data", ""))
                    for item in dns.get("Answer", [])
                    if item.get("type") == 1
                ]
                checks.append(
                    {
                        "title": "DNS ядра · example.com",
                        "state": "ok" if addresses else "error",
                        "detail": ", ".join(addresses) if addresses else "A-адреса не получены.",
                    }
                )
            except ClientError as exc:
                checks.append({"title": "DNS ядра", "state": "error", "detail": str(exc)})
            try:
                with httpx.Client(
                    proxy=f"http://127.0.0.1:{mixed}", trust_env=False, timeout=6
                ) as client:
                    data = client.get("https://ipwho.is/").raise_for_status().json()
                if data.get("success") is not True:
                    raise ValueError
                import ipaddress

                address = str(ipaddress.ip_address(data["ip"]))
                checks.append(
                    {
                        "title": "IP выхода проверочного запроса",
                        "state": "ok",
                        "detail": f"{address} · {data.get('country', '')} · {data.get('city', '')}",
                    }
                )
            except (httpx.HTTPError, ValueError, KeyError):
                checks.append(
                    {
                        "title": "HTTPS через прокси",
                        "state": "error",
                        "detail": "Проверочный запрос не завершён. Откройте журнал ядра.",
                    }
                )
            return checks

        self.start_inspection("network", check, self.show_checks)

    def restart_connection(self):
        if not self.connected or self.task:
            return

        def stopped(_):
            self.set_disconnected()
            self.pending_connect = True

        self.state.setText("Отключение…")
        self.run_task(lambda: self.core.stop("reconnect requested"), stopped)

    def edit_local_rule(self, editing=False):
        if not self.profile or self.task:
            return
        row = self.rule_table.currentRow()
        index = row if 0 <= row < len(self.profile.local_rules) else None
        if editing and index is None:
            return
        dialog = RuleDialog(
            self.profile, self.profile.local_rules[index] if editing else None, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rules = list(self.profile.local_rules)
        if editing:
            rules[index] = dialog.value
        else:
            rules.append(dialog.value)
        self.save_local_rules(rules)

    def delete_local_rule(self):
        if not self.profile or self.task:
            return
        row = self.rule_table.currentRow()
        if 0 <= row < len(self.profile.local_rules):
            self.save_local_rules(
                [rule for index, rule in enumerate(self.profile.local_rules) if index != row]
            )

    def save_local_rules(self, rules):
        profile = replace(self.profile, local_rules=rules)

        def saved(_):
            self.profile = profile
            self.library = self.library.upsert(profile)
            self.refresh_library()
            self.refresh_policy()
            self.rules_pending.setText(
                "Правила сохранены. Переподключитесь для применения."
                if self.connected
                else "Правила сохраняются отдельно от подписки."
            )
            self.log("Локальные правила сохранены.")

        self.run_task(lambda: self.store.save_profile(profile), saved)
