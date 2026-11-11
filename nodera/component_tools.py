"""Separate tools for route geolocation and routing database updates."""

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from nodera import geodata_updates
from nodera.geodata_updates import GeodataUpdates
from nodera.models import ClientError
from nodera.widgets import label


class ComponentToolsMixin:
    def build_component_tools(self):
        self.geodata_manager = GeodataUpdates(self.store.root)
        self.available_geodata_release = None
        self.geodata_page_index = self.stack.count()
        layout = self.page("Базы маршрутизации")
        layout.addWidget(label("GeoIP · GeoSite · Country · ASN", "profileName"))
        layout.addWidget(
            label(
                "Базы адресов и доменов для правил Mihomo. Обновляются отдельно от приложения и определения геолокации.",
                "muted",
                True,
            )
        )
        self.geodata_status = label(self.geodata_manager.version(), "muted", True)
        layout.addWidget(self.geodata_status)
        row = QHBoxLayout()
        self.geodata_check = QPushButton("Проверить обновления")
        self.geodata_check.clicked.connect(self.check_geodata_updates)
        self.geodata_install = QPushButton("Обновить базы")
        self.geodata_install.setObjectName("primary")
        self.geodata_install.clicked.connect(self.install_geodata_update)
        self.geodata_install.hide()
        self.geodata_rollback = QPushButton("Вернуть прежние базы")
        self.geodata_rollback.clicked.connect(self.rollback_geodata_update)
        self.geodata_rollback.setVisible(self.geodata_manager.selected() is not None)
        for button in (self.geodata_check, self.geodata_install, self.geodata_rollback):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(
            label(
                "Новые базы применяются при следующем подключении. Текущий сеанс сохраняет свою копию баз.",
                "muted",
                True,
            )
        )
        layout.addStretch()
        self.navigation.add_tool_page(self.geodata_page_index, "Базы маршрутизации", "routes")

        self.geolocation_page_index = self.stack.count()
        layout = self.page("Геолокация")
        self.geolocation_profile = label("", "muted", True)
        layout.addWidget(self.geolocation_profile)
        row = QHBoxLayout()
        self.geo_determine_button = QPushButton("Определить IP и страны маршрутов")
        self.geo_determine_button.setObjectName("primary")
        self.geo_determine_button.clicked.connect(
            lambda: self.check_locations(all_nodes=True, refresh=True)
        )
        row.addWidget(self.geo_determine_button)
        row.addWidget(self.geo_cancel_button)
        row.addStretch()
        layout.addLayout(row)
        self.geo_tool_status = label(
            "Определение IP и геолокации не обновляет базы маршрутизации.", "muted", True
        )
        layout.addWidget(self.geo_tool_status)
        addresses = self.server_tabs.widget(1)
        self.server_tabs.removeTab(1)
        layout.addWidget(addresses, 1)
        addresses.show()
        for index, width in enumerate((120, 105, 105, 120, 55, 85), 1):
            self.address_table.setColumnWidth(index, width)
        self.navigation.add_tool_page(self.geolocation_page_index, "Геолокация", "map")

        # Keep a discoverable shortcut from connections; detection lives in its own tool.
        shortcut = QWidget()
        column = QVBoxLayout(shortcut)
        column.addWidget(
            label(
                "IP, страны и участки маршрута доступны в отдельном инструменте «Геолокация».",
                "muted",
                True,
            )
        )
        open_geo = QPushButton("Открыть геолокацию")
        open_geo.clicked.connect(lambda: self.navigate(self.geolocation_page_index))
        column.addWidget(open_geo)
        column.addStretch()
        self.server_tabs.addTab(shortcut, "IP и маршрут")

    def geodata_busy(self):
        return bool({"geodata-check", "geodata-download"}.intersection(self.inspections))

    def geodata_controls(self, enabled):
        for button in (self.geodata_check, self.geodata_install, self.geodata_rollback):
            button.setEnabled(enabled)

    def schedule_geodata_check(self, milliseconds):
        timer = getattr(self, "geodata_update_timer", None)
        if timer is not None and not self.closing:
            timer.start(milliseconds)

    def check_geodata_updates(self):
        if self.closing:
            return
        if self.task or self.geodata_busy():
            self.schedule_geodata_check(60000)
            return
        self.geodata_controls(False)
        self.geodata_status.setText("Проверка обновлений баз…")
        current = self.geodata_manager.identity()
        proxy = self.update_proxy() or getattr(self, "subscription_proxy", None)

        def done(release):
            self.available_geodata_release = release
            self.geodata_controls(True)
            self.geodata_install.setVisible(release is not None)
            self.geodata_status.setText(
                f"Доступны базы {release.version}\nВыбраны: {self.geodata_manager.version()}"
                if release
                else f"Базы актуальны · {self.geodata_manager.version()}"
            )
            self.schedule_geodata_check(24 * 60 * 60 * 1000)

        def failed(message):
            self.geodata_controls(True)
            self.geodata_status.setText(message)
            self.schedule_geodata_check(30 * 60 * 1000)

        self.start_inspection(
            "geodata-check",
            lambda: geodata_updates.check_release(current, proxy=proxy),
            done,
            failed,
            session_bound=False,
        )

    def install_geodata_update(self):
        if self.closing or self.task or self.geodata_busy() or not self.available_geodata_release:
            return
        release = self.available_geodata_release
        proxy = self.update_proxy() or getattr(self, "subscription_proxy", None)
        self.geodata_controls(False)
        self.geodata_status.setText("Загрузка и проверка четырёх баз…")

        def failed(message):
            self.geodata_controls(True)
            self.geodata_status.setText(message)

        def downloaded(entry):
            try:
                self.geodata_manager.activate(entry)
            except (OSError, ValueError, KeyError, TypeError, ClientError):
                failed("Не удалось выбрать новые базы. Прежние базы сохранены.")
                return
            self.available_geodata_release = None
            self.geodata_install.hide()
            self.geodata_controls(True)
            self.geodata_rollback.show()
            self.geodata_status.setText(
                f"Базы {release.version} готовы. Применятся при следующем подключении."
            )

        self.start_inspection(
            "geodata-download",
            lambda: self.geodata_manager.download(
                release, proxy=proxy, cancelled=self.shutdown_event
            ),
            downloaded,
            failed,
            session_bound=False,
        )

    def rollback_geodata_update(self):
        if self.closing or self.task or self.geodata_busy():
            return
        try:
            self.geodata_manager.rollback()
        except (OSError, ValueError, KeyError, TypeError):
            self.geodata_status.setText("Не удалось вернуть прежние базы.")
            return
        self.geodata_rollback.setVisible(self.geodata_manager.selected() is not None)
        self.geodata_status.setText(
            f"Выбраны: {self.geodata_manager.version()}. Применятся при следующем подключении."
        )
