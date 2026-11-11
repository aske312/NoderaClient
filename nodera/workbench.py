"""Operational pages: small toolbars, stable lists and explicit background checks."""

import json
import re
import time
from copy import deepcopy
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
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nodera import __version__, autostart, updater
from nodera.bundled_core import core_manifest
from nodera.controller import Controller
from nodera.controls import ComboBox as QComboBox
from nodera.controls import SubscriptionSelector
from nodera.delay_probe import probe_delays
from nodera.device_info import windows_identity
from nodera.diagnostics import ipv6_route_check, preflight
from nodera.geolocation import endpoint_rows
from nodera.journal import compact_entries, format_entry, profile_secrets, useful_event
from nodera.locations import country
from nodera.models import ClientError, visible_proxies
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


def breakable_text(text):
    """Allow labels to wrap paths and long build IDs without dropping characters."""
    text = text.replace("\\", "\\\u200b").replace("/", "/\u200b")
    return re.sub(r"[^\s\u200b]{32}", lambda match: match[0] + "\u200b", text)


def wrapping_label(text, kind=""):
    widget = label(breakable_text(text), kind, True)
    policy = widget.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    policy.setHeightForWidth(True)
    widget.setSizePolicy(policy)
    widget.setMinimumWidth(0)
    return widget


class WorkbenchMixin:
    def refresh_autostart(self):
        if self.closing or "autostart" in self.inspections:
            return
        self.autostart_check.setEnabled(False)

        def done(enabled):
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(enabled)
            self.autostart_check.blockSignals(False)
            self.autostart_loaded = True

        def failed(message):
            self.autostart_loaded = False
            self.settings_result.setText(message)

        self.start_inspection("autostart", autostart.is_enabled, done, failed, session_bound=False)
        self.inspections["autostart"].finished.connect(
            lambda: self.autostart_check.setEnabled(self.autostart_loaded and not self.closing)
        )

    def change_autostart(self, enabled):
        if not self.autostart_loaded or self.closing or "autostart" in self.inspections:
            return
        self.autostart_check.setEnabled(False)

        def done(_):
            self.settings_result.setText("Автозапуск включён" if enabled else "Автозапуск выключен")

        def failed(message):
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(not enabled)
            self.autostart_check.blockSignals(False)
            self.settings_result.setText(message)

        self.start_inspection(
            "autostart",
            lambda: autostart.set_enabled(enabled),
            done,
            failed,
            session_bound=False,
        )
        self.inspections["autostart"].finished.connect(
            lambda: self.autostart_check.setEnabled(not self.closing)
        )

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
        layout = self.page("О программе")
        self.application_page = self.stack.widget(self.stack.count() - 1)
        panel = QWidget()
        panel.setObjectName("applicationCard")
        panel.setMaximumWidth(780)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.application_panel = panel
        content = QVBoxLayout(panel)
        content.setContentsMargins(18, 18, 18, 18)
        content.setSpacing(14)
        row = QHBoxLayout()
        icon = label("")
        icon.setPixmap(app_icon().pixmap(40, 40))
        icon.setFixedSize(40, 40)
        row.setSpacing(12)
        row.addWidget(icon)
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(label("Nodera", "dashboardProfile"))
        column.addWidget(label("Клиент для безопасных подключений на Windows", "muted", True))
        row.addLayout(column, 1)
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
        content.addLayout(row)
        self.application_version = wrapping_label(version_text(), "muted")
        self.application_version.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        content.addWidget(self.application_version)
        divider = QFrame()
        divider.setObjectName("aboutDivider")
        divider.setFixedHeight(1)
        content.addWidget(divider)
        self.application_details = QWidget()
        details = QVBoxLayout(self.application_details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(10)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(24)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.application_detail_values = {}
        for caption, text in (
            ("Ядро", self.application_values["Ядро"]),
            ("Система", self.application_values["Система"]),
            ("Режимы", self.application_values["Режимы"]),
            ("Данные", str(self.store.root)),
        ):
            value = wrapping_label(text)
            value.setAccessibleDescription(text)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label(caption, "muted"), value)
            self.application_detail_values[caption] = value
        details.addLayout(form)
        content.addWidget(self.application_details)
        copy_info = QPushButton("Копировать сведения")
        copy_info.setObjectName("quietAction")
        copy_info.clicked.connect(
            lambda: QApplication.clipboard().setText(
                "Nodera "
                + version_text()
                + "\n"
                + "\n".join(
                    f"{name}: {value}"
                    for name, value in self.application_values.items()
                    if name in {"Ядро", "Система", "Хранение"}
                )
                + f"\nДанные: {self.store.root}"
            )
        )
        self.application_copy_info = copy_info
        content.addWidget(copy_info, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(panel)
        updates_panel = QFrame()
        updates_panel.setObjectName("applicationUpdates")
        updates_panel.setMaximumWidth(780)
        update_content = QVBoxLayout(updates_panel)
        update_content.setContentsMargins(16, 14, 16, 14)
        update_content.setSpacing(8)
        self.available_release = None
        self.downloaded_update = None
        self.app_update_status = wrapping_label("Проверить наличие обновлений", "muted")
        updates = QHBoxLayout()
        updates.setSpacing(16)
        update_text = QVBoxLayout()
        update_text.setSpacing(4)
        update_text.addWidget(label("Обновления", "profileName"))
        update_text.addWidget(self.app_update_status)
        updates.addLayout(update_text, 1)
        self.app_update_check = QPushButton("Проверить")
        self.app_update_check.setObjectName("primary")
        self.app_update_check.clicked.connect(self.check_app_updates)
        self.app_update_install = QPushButton("Загрузить обновление")
        self.app_update_install.setObjectName("primary")
        self.app_update_install.hide()
        self.app_update_install.clicked.connect(self.install_app_update)
        updates.addWidget(self.app_update_check)
        updates.addWidget(self.app_update_install)
        update_content.addLayout(updates)
        self.app_update_progress = QProgressBar()
        self.app_update_progress.setObjectName("aboutProgress")
        self.app_update_progress.setRange(0, 0)
        self.app_update_progress.setTextVisible(False)
        self.app_update_progress.setFixedHeight(3)
        self.app_update_progress.hide()
        update_content.addWidget(self.app_update_progress)
        layout.addWidget(updates_panel)
        self.build_core_updates(layout)
        layout.addStretch(1)

    def update_proxy(self):
        return (
            f"http://127.0.0.1:{self.core.mixed_port}"
            if self.connected and self.core.mixed_port
            else None
        )

    def schedule_app_update_check(self, milliseconds):
        timer = getattr(self, "app_update_timer", None)
        if timer is not None and not self.closing:
            timer.start(milliseconds)

    def check_app_updates(self):
        if self.closing:
            return
        if self.task or {"app-update", "app-download"}.intersection(self.inspections):
            self.schedule_app_update_check(60000)
            return
        previous_release = self.available_release
        previous_download = self.downloaded_update
        self.app_update_install.setText("Загрузить обновление")
        self.app_update_install.hide()
        self.app_update_check.show()
        self.app_update_progress.show()
        self.app_update_check.setEnabled(False)
        self.app_update_install.setEnabled(False)
        self.app_update_status.setText("Проверка GitHub…")
        proxy = self.update_proxy()

        def checked(release):
            self.app_update_progress.hide()
            self.app_update_check.setVisible(release is None)
            self.schedule_app_update_check(6 * 60 * 60 * 1000)
            self.available_release = release
            self.downloaded_update = previous_download if release == previous_release else None
            self.app_update_install.setText(
                "Установить обновление" if self.downloaded_update else "Загрузить обновление"
            )
            self.app_update_check.setEnabled(True)
            self.app_update_install.setEnabled(True)
            self.app_update_install.setVisible(release is not None)
            self.app_update_status.setText(
                f"Доступна версия {release.version}"
                if release
                else f"Установлена актуальная версия {__version__}"
            )
            if release:
                self.notify_app_update(release)

        def failed(message):
            self.app_update_progress.hide()
            self.schedule_app_update_check(30 * 60 * 1000)
            self.app_update_check.setEnabled(True)
            self.app_update_install.setEnabled(self.available_release is not None)
            self.app_update_install.setVisible(self.available_release is not None)
            self.app_update_status.setText(message)

        self.start_inspection(
            "app-update",
            lambda: updater.check_release(proxy=proxy),
            checked,
            failed,
            session_bound=False,
        )

    def notify_app_update(self, release):
        from PySide6.QtWidgets import QSystemTrayIcon

        if release.version == getattr(self, "notified_update_version", None):
            return
        self.notified_update_version = release.version
        if self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self.update_notification_kind = "app"
            self.tray.showMessage(
                "Обновление Nodera",
                f"Доступна версия {release.version}. Нажмите, чтобы обновиться.",
                QSystemTrayIcon.MessageIcon.Information,
                10000,
            )
        if self.isVisible():
            self.show_update_offer()

    def show_update_offer(self):
        from nodera.modals import MessageDialog

        if self.closing or not self.available_release:
            return
        existing = getattr(self, "update_offer", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(3000, self.show_update_offer)
            return
        self.reveal()
        dialog = MessageDialog(
            self,
            "Доступно обновление",
            f"Nodera {self.available_release.version}\nУстановлена версия {__version__}.\nПодписки и настройки сохранятся.",
            "Установить" if self.downloaded_update else "Загрузить",
            cancel=True,
        )
        dialog.cancel_button.setText("Позже")
        dialog.accepted.connect(self.accept_update_offer)
        self.update_offer = dialog
        dialog.open()

    def accept_update_offer(self):
        self.navigate(self.stack.indexOf(self.application_page))
        self.install_app_update()

    def install_app_update(self):
        if (
            self.task
            or self.closing
            or not self.available_release
            or {"app-update", "app-download"}.intersection(self.inspections)
        ):
            return
        release, proxy = self.available_release, self.update_proxy()
        if self.downloaded_update is not None:
            try:
                updater.launch_installer(self.downloaded_update, release, resume=self.connected)
            except ClientError as exc:
                self.downloaded_update = None
                self.app_update_install.setText("Загрузить повторно")
                self.app_update_status.setText(str(exc))
                return
            self.app_update_status.setText(
                "Установщик открыт. Клиент продолжит работу до подтверждения установки. "
                "После установки нажмите «Открыть Nodera»; подключение будет восстановлено."
            )
            return
        self.app_update_check.setEnabled(False)
        self.app_update_install.setEnabled(False)
        self.app_update_status.setText("Загрузка и проверка установщика…")
        self.app_update_progress.show()

        def downloaded(path):
            self.app_update_progress.hide()
            self.downloaded_update = path
            self.app_update_check.setEnabled(True)
            self.app_update_install.setEnabled(True)
            self.app_update_install.setText("Установить обновление")
            self.app_update_status.setText(
                f"Версия {release.version} готова. Установка перезапустит клиент."
            )

        def failed(message):
            self.app_update_progress.hide()
            self.app_update_check.setEnabled(True)
            self.app_update_install.setEnabled(True)
            self.app_update_status.setText(message)

        self.start_inspection(
            "app-download",
            lambda: updater.download_release(
                release,
                self.store.root,
                proxy=proxy,
                cancelled=self.shutdown_event,
            ),
            downloaded,
            failed,
            session_bound=False,
        )

    def build_servers(self):
        layout = self.page("Подключения")
        self.connections_profile_selector = SubscriptionSelector()
        self.connections_profile_selector.currentIndexChanged.connect(
            lambda: self.switch_profile(self.connections_profile_selector.currentData())
        )
        bar = QHBoxLayout()
        bar.addWidget(self.connections_profile_selector)
        self.connections_pro_badge = label("PRO", "proBadge")
        bar.addWidget(self.connections_pro_badge)
        self.connections_strategy_tag = label("", "badge")
        bar.addWidget(self.connections_strategy_tag)
        self.group_combo = QComboBox(self)
        self.group_combo.setAccessibleName("Группа подключений")
        self.group_combo.currentIndexChanged.connect(self.render_nodes)
        self.group_combo.hide()
        self.server_search = QLineEdit()
        self.server_search.setPlaceholderText("Поиск по маршрутам и адресам")
        self.server_search.textChanged.connect(self.filter_nodes)
        self.server_sort = combo(
            [("Из подписки", "profile"), ("По имени", "name"), ("По задержке", "delay")], "profile"
        )
        self.server_sort.currentIndexChanged.connect(self.render_nodes)
        self.group_combo.setMaximumWidth(170)
        self.server_sort.setMaximumWidth(150)
        bar.addWidget(self.server_search, 2)
        bar.addWidget(self.server_sort)
        layout.addLayout(bar)
        self.server_tabs = QTabWidget()
        self.server_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.server_tabs.setMinimumHeight(160)
        self.node_list = NodeList()
        self.node_list.pin_requested.connect(self.toggle_server_pin)
        self.node_list.currentRowChanged.connect(self.server_selection_changed)
        self.server_tabs.addTab(self.node_list, "Маршруты")
        addresses = QWidget()
        column = QVBoxLayout(addresses)
        column.setContentsMargins(0, 10, 0, 0)
        self.address_table = QTableWidget(0, 7)
        self.address_table.setObjectName("routeAddresses")
        self.address_table.setShowGrid(False)
        self.address_table.setAlternatingRowColors(True)
        self.address_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.address_table.setFrameShape(QFrame.Shape.NoFrame)
        self.address_table.verticalHeader().setDefaultSectionSize(34)
        self.address_table.horizontalHeader().setHighlightSections(False)
        self.address_table.setHorizontalHeaderLabels(
            ["Участок / адрес", "IP", "Страна", "Город", "Сеть / ASN", "Кол-во", "Проверено"]
        )
        self.address_table.verticalHeader().hide()
        self.address_table.horizontalHeaderItem(5).setToolTip("Количество маршрутов с этим адресом")
        self.address_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.address_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.address_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.address_table.horizontalHeader().setStretchLastSection(False)
        for index, width in enumerate((210, 125, 140, 120, 170, 60, 100)):
            self.address_table.setColumnWidth(index, width)
        self.address_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.address_table.setWordWrap(False)
        column.addWidget(self.address_table, 1)
        copy_addresses = QPushButton("Копировать выбранные адреса")
        copy_addresses.clicked.connect(self.copy_addresses)
        column.addWidget(copy_addresses, 0, Qt.AlignmentFlag.AlignLeft)
        shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.address_table)
        shortcut.activated.connect(self.copy_addresses)
        self.address_note = label(
            "IP проверяются автоматически при подключении и обновлении подписки.", "muted", True
        )
        column.addWidget(self.address_note)
        self.server_tabs.addTab(addresses, "IP и маршрут")
        layout.addWidget(self.server_tabs, 1)
        self.select_button = QPushButton("Выбрать в группе")
        self.select_button.setObjectName("quietAction")
        self.select_button.clicked.connect(self.select_server)
        self.delay_button = QPushButton("Задержка")
        self.delay_button.setToolTip(
            "HTTPS-проверка всех маршрутов подписки. Автоматически — каждые 15 секунд при подключении."
        )
        self.delay_button.clicked.connect(self.test_group)
        self.location_button = QPushButton("Редактировать маршрут")
        self.location_button.clicked.connect(self.choose_location)
        self.refresh_servers_button = QPushButton("Обновить")
        self.refresh_servers_button.clicked.connect(self.reload_servers)
        self.delay_all_button = self.delay_button
        self.geo_cancel_button = QPushButton("Остановить")
        self.geo_cancel_button.clicked.connect(self.cancel_geo)
        self.geo_cancel_button.setVisible(False)
        checks = QHBoxLayout()
        for button in (self.delay_button, self.geo_cancel_button):
            checks.addWidget(button)
        self.delay_status = label("Задержка · Фоновая проверка каждые 15 с", "muted", True)
        checks.addWidget(self.delay_status, 1)
        checks.addWidget(self.refresh_servers_button)
        checks_panel = QFrame()
        checks_panel.setObjectName("serverChecks")
        checks_panel.setLayout(checks)
        checks.setContentsMargins(10, 8, 10, 8)
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
        layout.addWidget(checks_panel)
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
        profile, choices = self.display_profile(), self.current_choices()
        for (profile_id, name), value in self.geo_results.items():
            if profile_id == self.profile.id and value.get("endpoints") == endpoint_rows(
                profile, name, choices
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

    def schedule_location_check(self, profile=None):
        profile = profile or self.display_profile()
        if self.closing or not profile or not self.settings.geo_auto:
            return
        self.pending_geo_profiles[profile.id] = deepcopy(profile)
        if self.geo_task and self.geo_task.profile.id == profile.id:
            self.geo_task.cancelled.set()
        QTimer.singleShot(0, self.run_pending_locations)

    def refresh_location_views(self):
        self.geo_render_timer.stop()
        if not self.closing:
            self.render_nodes()
            self.update_route_pill()

    def run_pending_locations(self):
        if self.closing or self.geo_task:
            return
        while self.pending_geo_profiles:
            profile_id = next(iter(self.pending_geo_profiles))
            profile = self.pending_geo_profiles.pop(profile_id)
            if not any(item.id == profile_id for item in self.library.profiles):
                continue
            self.check_locations(
                all_nodes=True, automatic=True, profile_override=profile, refresh=True
            )
            if self.geo_task:
                break

    def check_locations(
        self, all_nodes=False, automatic=False, profile_override=None, refresh=False
    ):
        profile = profile_override or self.display_profile()
        if self.closing or self.geo_task or not profile:
            return
        choices = (
            self.current_choices()
            if self.profile and profile.id == self.profile.id
            else profile.selections
        )
        names = (
            [item["name"] for item in visible_proxies(profile.config)]
            if all_nodes
            else [self.current_node()]
        )
        if automatic and not all_nodes:
            names = list(
                dict.fromkeys(
                    name for group in self.groups_data for name in group.get("active", [])
                )
            )[:4]
        names = [name for name in names if name and endpoint_rows(profile, name, choices)]
        if automatic and not refresh:
            names = [
                name
                for name in names
                if time.time() - self.geo_results.get((profile.id, name), {}).get("timestamp", 0)
                > 300
                or self.geo_results.get((profile.id, name), {}).get("endpoints")
                != endpoint_rows(profile, name, choices)
            ]
        if not names:
            if not automatic:
                self.server_result.setText(
                    "Адрес маршрута не передан. Укажите его в редакторе маршрута."
                )
            return
        proxy = (
            f"http://127.0.0.1:{self.core.mixed_port}"
            if self.connected and self.core.mixed_port
            else getattr(self, "subscription_proxy", None)
        )
        self.geo_client.proxy = proxy
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
        if hasattr(self, "geo_determine_button"):
            self.geo_determine_button.setEnabled(False)
            self.geo_tool_status.setText("Определение IP и геолокации маршрутов…")
        self.geo_cancel_button.setEnabled(True)
        self.geo_cancel_button.setVisible(True)
        self.server_result.setText("Проверка IP в фоне…")

        def checked(name, value):
            if self.closing:
                return
            latest = next((item for item in self.library.profiles if item.id == profile.id), None)
            if latest is None or endpoint_rows(latest, name, choices) != endpoint_rows(
                profile, name, choices
            ):
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
                if not self.geo_render_timer.isActive():
                    self.geo_render_timer.start(80)

        task.progress.connect(checked)
        task.message.connect(lambda message: self.log(message, "warning", "Геолокация"))

        def finished():
            if self.geo_task is task:
                self.geo_task = None
            task.deleteLater()
            self.refresh_location_views()
            self.geo_cancel_button.setEnabled(False)
            self.geo_cancel_button.setVisible(False)
            if hasattr(self, "geo_determine_button"):
                self.geo_determine_button.setEnabled(bool(self.profile and not self.closing))
                self.geo_tool_status.setText(
                    "Определение завершено"
                    if not task.cancelled.is_set()
                    else "Определение остановлено"
                )
            self.server_result.setText(
                "Проверка IP завершена" if not task.cancelled.is_set() else "Проверка остановлена"
            )
            if self.closing:
                QTimer.singleShot(0, self.close)
            elif self.pending_geo_profiles:
                QTimer.singleShot(0, self.run_pending_locations)

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
        names = [item["name"] for item in visible_proxies(self.display_profile().config)]
        if not names:
            return
        port, secret = self.core.port, self.core.secret
        profile_id = self.profile.id
        generation = self.session_generation
        core = self.core
        health = core.route_health
        self.delay_status.setText(f"Проверка всех маршрутов: {len(names)}…")

        def checked(values):
            self.delay_results.update({(profile_id, name): value for name, value in values.items()})
            self.record_route_results(profile_id, values)
            for group in self.groups_data:
                for item in group["members"]:
                    if item["name"] in values:
                        item["delay"] = values[item["name"]]["value"]
            self.render_nodes()
            self.update_network_tag()
            available = sum(value["value"] > 0 for value in values.values())
            self.delay_status.setText(
                f"{datetime.now():%H:%M:%S} · Доступно {available}/{len(values)} · Авто: 15 с"
            )

        def failed(message):
            self.delay_status.setText("Проверка задержек не завершена")
            self.log(message, "warning")

        def check():
            aliases = core.probe_aliases()

            def cancelled():
                return self.closing or generation != self.session_generation

            values = probe_delays(port, secret, names, cancelled, aliases)
            if not cancelled():
                if getattr(core, "demo", False):
                    core.update_route_health(values)
                else:
                    core.update_route_health(values, expected=health)
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
            self.recover_routes(proactive=True)
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
            auto_connect=self.auto_connect_check.isChecked(),
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
        self.flow_timer.setInterval(settings.flow_poll_seconds * 1000)
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
            prefix = {
                "ok": "ГОТОВО",
                "error": "ОШИБКА",
                "info": "ИНФОРМАЦИЯ",
                "warning": "ВНИМАНИЕ",
            }[check["state"]]
            self.diagnostic_list.addItem(
                QListWidgetItem(f"{prefix} · {check['title']}\n{check['detail']}")
            )
            self.log(
                f"{check['title']}: {check['detail']}",
                check["state"] if check["state"] in {"error", "warning"} else "info",
                "Диагностика",
            )
        self.log(f"Проверок завершено: {len(checks)}.", source="Диагностика")

    def diagnose(self):
        if self.closing:
            return
        settings, profile, running = self.settings, self.profile, self.core.running
        self.start_inspection(
            "local",
            lambda: preflight(settings, profile, running, root=self.store.root),
            self.show_checks,
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
            ipv6 = ipv6_route_check(config.get("ipv6") is True)
            if ipv6:
                checks.append(ipv6)
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
