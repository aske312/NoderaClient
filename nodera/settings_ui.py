"""Padded setting rows, switches, and client map-point controls."""

from dataclasses import replace
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nodera.client_location import detect_client_location
from nodera.controls import ComboBox, CoordinateBox, NumberBox, Switch
from nodera.locations import country, map_data
from nodera.server_assets import flag_pixmap
from nodera.widgets import label


def selector(values, current):
    widget = ComboBox()
    for caption, value in values:
        widget.addItem(caption, value)
    widget.setCurrentIndex(max(0, widget.findData(current)))
    widget.setFixedWidth(230)
    return widget


def section(column, title):
    frame = QFrame()
    frame.setObjectName("settingsCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(0)
    heading = label(title, "eyebrow")
    heading.setContentsMargins(14, 10, 14, 2)
    layout.addWidget(heading)
    column.addWidget(frame)
    return layout


def setting(layout, caption, field, detail=""):
    frame = QWidget()
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 7, 14, 7)
    row.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(2)
    caption_label = label(caption, wrap=True)
    caption_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    text.addWidget(caption_label)
    if detail:
        text.addWidget(label(detail, "muted", True))
    row.addLayout(text, 1)
    row.addWidget(field, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    field.setAccessibleName(caption)
    field.setting_row = frame
    layout.addWidget(frame)


def number(value, maximum, step=1):
    widget = NumberBox()
    widget.setRange(0, maximum)
    widget.setSingleStep(step)
    widget.setValue(value)
    widget.setFixedWidth(150)
    return widget


def switch(value):
    widget = Switch()
    widget.setChecked(value)
    return widget


def build_settings(self):
    layout = self.page("Настройки")
    self.settings_tabs = QTabWidget()
    layout.addWidget(self.settings_tabs, 1)
    pages = []
    for title in ("Основное", "Сеть", "Карта", "Запись логов"):
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(4, 8, 4, 4)
        column.setSpacing(8)
        self.settings_tabs.addTab(page, title)
        pages.append(column)
    general, network, mapping, logging = pages
    form = section(general, "Подключение и приложение")
    self.connection_mode = selector(
        [("TUN (VPN)", "tun"), ("Proxy (HTTP / SOCKS5)", "proxy")],
        "tun" if self.settings.tun else "proxy",
    )
    self.connection_mode.currentIndexChanged.connect(lambda *_: self.refresh_controls())
    setting(form, "Режим подключения", self.connection_mode)
    self.system_proxy_check = switch(self.settings.system_proxy)
    setting(
        form,
        "Системный прокси",
        self.system_proxy_check,
        "Для приложений, использующих настройки Windows",
    )
    self.close_tray_check = switch(self.settings.close_to_tray)
    setting(form, "Закрывать окно в трей", self.close_tray_check)
    self.autostart_check = switch(False)
    self.autostart_check.setEnabled(False)
    self.autostart_loaded = False
    self.autostart_check.toggled.connect(self.change_autostart)
    setting(
        form,
        "Запускать с Windows",
        self.autostart_check,
        "В трее при входе в систему · Применяется сразу",
    )
    self.update_interval = selector(
        [
            ("Вручную", 0),
            ("Каждый час", 1),
            ("Каждые 6 часов", 6),
            ("Каждые 12 часов", 12),
            ("Раз в сутки", 24),
        ],
        self.settings.update_hours,
    )
    setting(form, "Обновление подписок", self.update_interval)
    form = section(general, "Наблюдение за соединениями")
    self.poll_interval = selector(
        [(f"{value} с", value) for value in (1, 3, 5, 10)], self.settings.poll_seconds
    )
    setting(form, "Опрос в фоне", self.poll_interval)
    self.flow_poll_setting = selector(
        [(f"{value} с", value) for value in (1, 2, 3, 5, 10)], self.settings.flow_poll_seconds
    )
    setting(form, "Опрос на странице соединений", self.flow_poll_setting)
    self.flow_history_setting = selector(
        [(f"{value} мин", value) for value in (1, 5, 15, 30)], self.settings.flow_history_minutes
    )
    setting(form, "Хранить завершённые в памяти", self.flow_history_setting)
    general.addStretch()

    form = section(network, "Локальная сеть")
    self.port = number(self.settings.mixed_port, 65535)
    self.port.setSpecialValueText("Автоматически")
    setting(form, "Порт HTTP / SOCKS5", self.port)
    self.stack_setting = selector(
        [("Из профиля", "profile"), ("Mixed", "mixed"), ("System", "system"), ("gVisor", "gvisor")],
        self.settings.tun_stack,
    )
    setting(form, "Стек TUN", self.stack_setting)
    self.mtu_setting = number(self.settings.tun_mtu, 9000)
    self.mtu_setting.setSpecialValueText("Из профиля")
    setting(form, "MTU", self.mtu_setting)
    self.ipv6_setting = selector(
        [("Из профиля", "profile"), ("Включён", "on"), ("Выключен", "off")], self.settings.ipv6_mode
    )
    setting(form, "IPv6", self.ipv6_setting)
    self.geo_auto_check = switch(self.settings.geo_auto)
    setting(
        form,
        "Проверять IP активных маршрутов",
        self.geo_auto_check,
        "ipwho.is · Приблизительная страна и город по IP",
    )
    flush = self.flush_dns_button = QPushButton("Очистить")
    flush.clicked.connect(
        lambda: (
            self.run_task(self.core.flush_dns, lambda _: self.log("DNS-кэш очищен."))
            if self.connected
            else self.log("Сначала подключитесь.", "warning")
        )
    )
    setting(form, "DNS-кэш ядра", flush)
    network_note = label("Изменения портов, TUN и IPv6 — при следующем подключении.", "muted", True)
    network_note.setContentsMargins(16, 4, 16, 4)
    network.addWidget(network_note)
    network.addStretch()

    form = section(mapping, "Карта маршрута")
    self.map_auto_zoom_check = switch(self.settings.map_auto_zoom)
    setting(form, "Приближать карту к точкам", self.map_auto_zoom_check)
    self.client_country_setting = ComboBox()
    self.client_country_setting.setFixedWidth(230)
    self.client_country_setting.addItem(QIcon(flag_pixmap("xx")), "Точка не задана", "")
    for code, place in sorted(map_data()["countries"].items(), key=lambda pair: pair[1]["name"]):
        self.client_country_setting.addItem(QIcon(flag_pixmap(code)), place["name"], code)
    setting(form, "Страна клиента", self.client_country_setting)
    self.client_name_setting = QLineEdit()
    self.client_name_setting.setFixedWidth(230)
    self.client_name_setting.setMaxLength(80)
    self.client_name_setting.setPlaceholderText("Город / подпись")
    setting(form, "Подпись точки", self.client_name_setting)
    coordinates = QWidget()
    coordinate_row = QHBoxLayout(coordinates)
    coordinate_row.setContentsMargins(0, 0, 0, 0)
    self.client_lat_setting, self.client_lon_setting = CoordinateBox(), CoordinateBox()
    for field, limit in ((self.client_lat_setting, 90), (self.client_lon_setting, 180)):
        field.setRange(-limit, limit)
        field.setDecimals(4)
        field.setSingleStep(0.01)
        field.setFixedWidth(110)
        coordinate_row.addWidget(field)
    self.client_lat_setting.setAccessibleName("Широта клиента")
    self.client_lon_setting.setAccessibleName("Долгота клиента")
    setting(form, "Широта / долгота", coordinates)
    self.client_coordinates = coordinates
    self.client_point_source = self.settings.client_point.get("source", "manual")
    self.client_detected_at = self.settings.client_point.get("detected_at", "")
    apply_point(self, self.settings.client_point)
    self.client_country_setting.currentIndexChanged.connect(lambda *_: manual_country(self))
    for signal in (
        self.client_name_setting.textEdited,
        self.client_lat_setting.valueChanged,
        self.client_lon_setting.valueChanged,
    ):
        signal.connect(lambda *_: mark_manual(self))
    self.detect_client_button = QPushButton("Определить по IP")
    self.detect_client_button.clicked.connect(lambda: detect_point(self))
    setting(form, "Местоположение клиента", self.detect_client_button)
    self.client_point_result = label(
        "По IP, приблизительно. Определяется один раз автоматически; можно уточнить вручную.",
        "muted",
        True,
    )
    mapping.addWidget(self.client_point_result)
    self.client_point_result.setContentsMargins(16, 4, 16, 4)
    mapping.addStretch()

    form = section(logging, "Журнал приложения")
    self.log_level_setting = selector(
        [
            ("Обычный (info)", "info"),
            ("Подробный (debug)", "debug"),
            ("Предупреждения", "warning"),
            ("Ошибки", "error"),
        ],
        self.settings.log_level,
    )
    setting(form, "Уровень ядра", self.log_level_setting)
    self.log_limit_setting = number(self.settings.log_limit, 20000, 500)
    self.log_limit_setting.setMinimum(500)
    setting(form, "Записей в интерфейсе", self.log_limit_setting)
    self.log_save_check = switch(self.settings.save_logs)
    setting(form, "Сохранять журнал приложения", self.log_save_check)
    form = section(logging, "Отдельный журнал соединений")
    self.connection_log_check = switch(self.settings.save_connection_logs)
    setting(
        form,
        "Маршрут и трафик каждого потока",
        self.connection_log_check,
        "connections.jsonl · Приложение, домен, маршрут, байты",
    )
    self.connection_log_size = selector(
        [(f"{value} МБ", value) for value in (4, 8, 16, 32)], self.settings.connection_log_mb
    )
    setting(
        form,
        "Размер файла",
        self.connection_log_size,
        "Три предыдущих файла сохраняются при ротации",
    )
    self.connection_log_interval_setting = selector(
        [(f"{value} с", value) for value in (5, 15, 30, 60)], self.settings.connection_log_interval
    )
    setting(form, "Запись трафика активных потоков", self.connection_log_interval_setting)
    open_logs = QPushButton("Открыть папку логов")

    def open_folder():
        self.journal.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.journal.root)))

    open_logs.clicked.connect(open_folder)
    setting(form, "Файлы журналов", open_logs)
    self.connection_log_check.toggled.connect(
        lambda enabled: [
            field.setting_row.setEnabled(enabled)
            for field in (self.connection_log_size, self.connection_log_interval_setting)
        ]
    )
    for field in (self.connection_log_size, self.connection_log_interval_setting):
        field.setting_row.setEnabled(self.connection_log_check.isChecked())
    logging.addStretch()
    row = QHBoxLayout()
    self.save_button = QPushButton("Сохранить")
    self.save_button.setObjectName("primary")
    self.save_button.clicked.connect(self.save_settings)
    row.addWidget(self.save_button)
    self.settings_result = label("", "muted", True)
    row.addWidget(self.settings_result, 1)
    layout.addLayout(row)
    self.network_settings_controls = [
        self.connection_mode,
        self.port,
        self.system_proxy_check,
        self.stack_setting,
        self.mtu_setting,
        self.ipv6_setting,
    ]


def apply_point(self, point):
    self._loading_client_point = True
    self.client_country_setting.setCurrentIndex(
        max(0, self.client_country_setting.findData(point.get("code", "")))
    )
    self.client_name_setting.setText(point.get("name", ""))
    self.client_lat_setting.setValue(point.get("lat", 0))
    self.client_lon_setting.setValue(point.get("lon", 0))
    for field in (self.client_name_setting, self.client_lat_setting, self.client_lon_setting):
        field.setEnabled(bool(point))
    self.client_name_setting.setting_row.setEnabled(bool(point))
    self.client_coordinates.setting_row.setEnabled(bool(point))
    self._loading_client_point = False


def mark_manual(self):
    if not self._loading_client_point:
        self.client_point_source = "manual"
        self.client_detected_at = ""


def manual_country(self):
    if self._loading_client_point:
        return
    place = country(self.client_country_setting.currentData())
    apply_point(self, place or {})
    mark_manual(self)


def client_point_value(self):
    code = self.client_country_setting.currentData()
    if not code:
        return {}
    return {
        "code": code,
        "lat": self.client_lat_setting.value(),
        "lon": self.client_lon_setting.value(),
        "name": self.client_name_setting.text().strip() or country(code)["name"],
        "source": self.client_point_source,
        "detected_at": self.client_detected_at,
    }


def detect_point(self, automatic=False):
    if self.closing or "client-location" in self.inspections:
        return
    original_point = dict(self.settings.client_point)
    if automatic:
        settings = replace(self.settings, client_location_attempted=True)
        try:
            self.store.save_settings(settings)
        except OSError:
            return
        self.settings = settings
    self.detect_client_button.setEnabled(False)
    self.client_point_result.setText("Определение через физический сетевой интерфейс…")

    def done(point):
        if automatic and (
            self.settings.client_point != original_point
            or self.client_point_source == "manual"
            and client_point_value(self)
        ):
            self.detect_client_button.setEnabled(True)
            return
        apply_point(self, point)
        self.client_point_source = point["source"]
        self.client_detected_at = point.get("detected_at", datetime.now(timezone.utc).isoformat())
        self.detect_client_button.setEnabled(True)
        self.client_point_result.setText(
            "Приблизительно: " + point["name"] + ". Сохраните настройки для отображения на карте."
        )
        if automatic:
            settings = replace(self.settings, client_point=client_point_value(self))
            try:
                self.store.save_settings(settings)
            except OSError:
                self.client_point_result.setText(
                    "Не удалось сохранить точку клиента. Нажмите «Сохранить»."
                )
                return
            self.settings = settings
            self.refresh_dashboard()
            self.client_point_result.setText("По IP: " + point["name"] + " · Сохранено")

    def failed(message):
        self.detect_client_button.setEnabled(True)
        self.client_point_result.setText(message)

    self.start_inspection(
        "client-location", detect_client_location, done, failed, session_bound=False
    )
