"""Mihomo update controls and nonblocking release notifications."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QPushButton, QVBoxLayout

from nodera import core_updates
from nodera.core_updates import CoreUpdates
from nodera.models import ClientError
from nodera.widgets import label


class CoreUpdatesMixin:
    def build_core_updates(self, layout):
        self.core_manager = CoreUpdates(self.store.root)
        self.available_core_release = None
        panel = QFrame()
        panel.setObjectName("applicationUpdates")
        panel.setMaximumWidth(780)
        content = QVBoxLayout(panel)
        content.setContentsMargins(16, 12, 16, 12)
        content.setSpacing(8)
        content.addWidget(label("Ядро Mihomo", "profileName"))
        self.core_update_status = label("", "muted", True)
        content.addWidget(self.core_update_status)
        row = QHBoxLayout()
        self.core_update_check = QPushButton("Проверить")
        self.core_update_check.clicked.connect(self.check_core_updates)
        self.core_update_install = QPushButton("Обновить ядро")
        self.core_update_install.setObjectName("primary")
        self.core_update_install.clicked.connect(self.install_core_update)
        self.core_update_install.hide()
        self.core_update_rollback = QPushButton("Вернуть прежнее ядро")
        self.core_update_rollback.clicked.connect(self.rollback_core_update)
        for button in (self.core_update_check, self.core_update_install, self.core_update_rollback):
            row.addWidget(button)
        row.addStretch()
        content.addLayout(row)
        layout.addWidget(panel)
        self.refresh_core_version()

    def refresh_core_version(self, detail=""):
        version = self.core_manager.version()
        value = "Mihomo " + version
        running = getattr(self, "running_core_version", "")
        if self.connected and running and running.removeprefix("v") != version:
            value += f" · работает {running}"
        self.application_values["Ядро"] = value
        self.application_detail_values["Ядро"].setText(value)
        self.core_update_status.setText(
            detail or f"Выбрано ядро {version}. Обновляется отдельно от Nodera."
        )
        self.core_update_rollback.setVisible(self.core_manager.selected() is not None)

    def schedule_core_update_check(self, milliseconds):
        timer = getattr(self, "core_update_timer", None)
        if timer is not None and not self.closing:
            timer.start(milliseconds)

    def core_update_busy(self):
        return bool({"core-update", "core-download"}.intersection(self.inspections))

    def core_update_controls(self, enabled):
        for button in (self.core_update_check, self.core_update_install, self.core_update_rollback):
            button.setEnabled(enabled)

    def check_core_updates(self):
        if self.closing:
            return
        if self.task or self.core_update_busy():
            self.schedule_core_update_check(60000)
            return
        self.core_update_controls(False)
        self.core_update_status.setText("Проверка обновлений Mihomo…")
        current = self.core_manager.version()
        proxy = self.update_proxy() or getattr(self, "subscription_proxy", None)

        def checked(release):
            self.available_core_release = release
            self.core_update_controls(True)
            self.core_update_install.setVisible(release is not None)
            self.refresh_core_version(
                f"Доступно ядро {release.version} · выбрано {self.core_manager.version()}"
                if release
                else f"Выбрано актуальное ядро {self.core_manager.version()}."
            )
            self.schedule_core_update_check(6 * 60 * 60 * 1000)
            if release:
                self.notify_core_update(release)

        def failed(message):
            self.core_update_controls(True)
            self.core_update_status.setText(message)
            self.schedule_core_update_check(30 * 60 * 1000)

        self.start_inspection(
            "core-update",
            lambda: core_updates.check_release(current, proxy=proxy),
            checked,
            failed,
            session_bound=False,
        )

    def notify_core_update(self, release):
        from PySide6.QtWidgets import QSystemTrayIcon

        if release.version == getattr(self, "notified_core_version", None):
            return
        self.notified_core_version = release.version
        if self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self.update_notification_kind = "core"
            self.tray.showMessage(
                "Обновление ядра Mihomo",
                f"Доступна версия {release.version}. Нажмите для просмотра.",
                QSystemTrayIcon.MessageIcon.Information,
                10000,
            )
        if self.isVisible():
            self.show_core_update_offer()

    def show_core_update_offer(self):
        from nodera.modals import MessageDialog

        if self.closing or not self.available_core_release:
            return
        existing = getattr(self, "core_update_offer", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            return
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(3000, self.show_core_update_offer)
            return
        self.reveal()
        dialog = MessageDialog(
            self,
            "Обновление ядра Mihomo",
            f"Доступна версия {self.available_core_release.version}.\n"
            "Новое ядро будет применено при следующем подключении.",
            "Загрузить",
            cancel=True,
        )
        dialog.cancel_button.setText("Позже")

        def accepted():
            self.navigate(self.stack.indexOf(self.application_page))
            self.install_core_update()

        dialog.accepted.connect(accepted)
        self.core_update_offer = dialog
        dialog.open()

    def show_update_notification(self):
        if getattr(self, "update_notification_kind", None) == "core":
            self.show_core_update_offer()
        else:
            self.show_update_offer()

    def install_core_update(self):
        if self.closing or self.task or self.core_update_busy() or not self.available_core_release:
            return
        release = self.available_core_release
        self.core_update_controls(False)
        self.core_update_status.setText("Загрузка и проверка ядра Mihomo…")
        proxy = self.update_proxy() or getattr(self, "subscription_proxy", None)

        def failed(message):
            self.core_update_controls(True)
            self.core_update_status.setText(message)

        def downloaded(entry):
            try:
                self.core_manager.activate(entry)
            except (ClientError, OSError, ValueError, KeyError, TypeError):
                failed("Не удалось выбрать новое ядро. Прежнее ядро сохранено.")
                return
            self.available_core_release = None
            self.core_update_install.hide()
            self.core_update_controls(True)
            self.refresh_core_version(
                f"Ядро {release.version} готово. Будет применено при следующем подключении."
            )
            self.log(
                f"Mihomo {release.version} загружено и проверено. Применится при следующем подключении."
            )

        self.start_inspection(
            "core-download",
            lambda: self.core_manager.download(release, proxy=proxy, cancelled=self.shutdown_event),
            downloaded,
            failed,
            session_bound=False,
        )

    def rollback_core_update(self):
        if self.closing or self.task or self.core_update_busy():
            return
        try:
            self.core_manager.rollback()
        except (OSError, ValueError, KeyError, TypeError):
            self.core_update_status.setText("Не удалось вернуть прежнее ядро.")
            return
        self.refresh_core_version("Прежнее ядро выбрано. Применится при следующем подключении.")
