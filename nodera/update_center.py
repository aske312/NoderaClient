"""One update offer for Nodera, Mihomo and the routing databases."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
)

from nodera import core_updates, geodata_updates, updater
from nodera.core_updates import CoreUpdates
from nodera.geodata_updates import GeodataUpdates
from nodera.modals import MessageDialog
from nodera.models import ClientError
from nodera.widgets import label

COMPONENTS = {"app": "Nodera", "core": "Ядро Mihomo", "geodata": "Базы правил"}
OPERATIONS = {"updates-check", "updates-apply", "core-rollback", "geodata-rollback"}
UPDATE_ERRORS = (ClientError, OSError, ValueError, KeyError, TypeError)


class UpdateCenterMixin:
    app_updates_enabled = True

    def build_updates(self, layout):
        self.core_manager = CoreUpdates(self.store.root)
        self.geodata_manager = GeodataUpdates(self.store.root)
        self.available_updates = {}
        self.downloaded_update = None
        self.notified_updates = ()
        self.component_update_notes = []
        panel = QFrame()
        panel.setObjectName("applicationUpdates")
        panel.setMaximumWidth(780)
        content = QVBoxLayout(panel)
        content.setContentsMargins(16, 14, 16, 14)
        content.setSpacing(8)
        content.addWidget(label("Обновления", "profileName"))
        self.update_status = label(
            "Nodera, ядро Mihomo и базы правил."
            if self.app_updates_enabled
            else "Ядро Mihomo и базы правил. Nodera Test обновляется через локальную сборку.",
            "muted",
            True,
        )
        content.addWidget(self.update_status)
        actions = QHBoxLayout()
        self.update_check = QPushButton("Проверить обновления")
        self.update_check.clicked.connect(lambda: self.check_updates())
        self.update_apply = QPushButton("Обновить")
        self.update_apply.setObjectName("primary")
        self.update_apply.clicked.connect(self.apply_updates)
        self.update_apply.hide()
        actions.addWidget(self.update_check)
        actions.addWidget(self.update_apply)
        actions.addStretch()
        content.addLayout(actions)
        self.update_progress = QProgressBar()
        self.update_progress.setObjectName("aboutProgress")
        self.update_progress.setRange(0, 0)
        self.update_progress.setTextVisible(False)
        self.update_progress.setFixedHeight(3)
        self.update_progress.hide()
        content.addWidget(self.update_progress)
        rollback = QHBoxLayout()
        self.core_update_rollback = QPushButton("Вернуть прежнее ядро")
        self.core_update_rollback.clicked.connect(self.rollback_core_update)
        self.geodata_rollback = QPushButton("Вернуть прежние базы")
        self.geodata_rollback.clicked.connect(self.rollback_geodata_update)
        rollback.addWidget(self.core_update_rollback)
        rollback.addWidget(self.geodata_rollback)
        rollback.addStretch()
        content.addLayout(rollback)
        layout.addWidget(panel)
        self.refresh_core_version()
        self.geodata_rollback.setVisible(bool(self.geodata_manager.state().get("active")))

    def refresh_core_version(self):
        version = self.core_manager.version()
        value = "Mihomo " + version
        running = self.running_core_version
        if self.connected and running and running.removeprefix("v") != version:
            value += f" · работает {running}"
        self.application_values["Ядро"] = value
        self.application_detail_values["Ядро"].setText(value)
        self.core_update_rollback.setVisible(bool(self.core_manager.state().get("active")))

    def update_busy(self):
        return bool(self.task or OPERATIONS.intersection(self.inspections))

    def set_update_busy(self, busy):
        self.update_progress.setVisible(busy)
        for button in (
            self.update_check,
            self.update_apply,
            self.core_update_rollback,
            self.geodata_rollback,
        ):
            button.setEnabled(not busy)
        self.update_apply.setVisible(bool(self.available_updates))
        self.update_apply.setText("Установить Nodera" if self.downloaded_update else "Обновить")

    def schedule_update_check(self, milliseconds):
        timer = getattr(self, "update_timer", None)
        if timer is not None and not self.closing:
            timer.start(milliseconds)

    def check_updates(self, *, automatic=False):
        if self.closing:
            return
        if self.update_busy():
            if automatic:
                self.schedule_update_check(60000)
            return
        self.set_update_busy(True)
        self.update_status.setText("Проверка обновлений…")
        proxy = self.update_proxy()
        component_proxy = proxy or getattr(self, "subscription_proxy", None)
        checks = {
            "core": lambda: core_updates.check_release(
                self.core_manager.version(), proxy=component_proxy
            ),
            "geodata": lambda: geodata_updates.check_release(
                self.geodata_manager.identity(verify=not automatic), proxy=component_proxy
            ),
        }
        if self.app_updates_enabled:
            checks["app"] = lambda: updater.check_release(proxy=proxy)

        def checked(result):
            releases, errors = result
            for key, release in releases.items():
                if key == "app" and release != self.available_updates.get("app"):
                    self.downloaded_update = None
                    self.component_update_notes = []
                self.available_updates.pop(key, None)
                if release is not None:
                    self.available_updates[key] = release
            self.schedule_update_check((30 if errors else 360) * 60 * 1000)
            offer = self.update_list() or ("Все компоненты актуальны." if not errors else "")
            if self.downloaded_update:
                offer = "Установщик Nodera загружен. Нажмите «Установить Nodera»."
            self.update_status.setText("\n".join(filter(None, [offer, *errors])))
            self.set_update_busy(False)
            self.notify_updates()

        def failed(message):
            self.schedule_update_check(30 * 60 * 1000)
            self.update_status.setText(message)
            self.set_update_busy(False)

        self.start_inspection(
            "updates-check",
            lambda: self.run_update_steps(checks),
            checked,
            failed,
            session_bound=False,
        )

    def run_update_steps(self, steps):
        results, errors = {}, []
        for key, step in steps.items():
            if self.shutdown_event.is_set():
                break
            try:
                results[key] = step()
            except UPDATE_ERRORS as exc:
                errors.append(f"{COMPONENTS[key]} · {exc}")
        return results, errors

    def update_list(self):
        return "\n".join(
            f"{name} {self.available_updates[key].version}"
            for key, name in COMPONENTS.items()
            if key in self.available_updates
        )

    def notify_updates(self):
        versions = tuple(
            (key, self.available_updates[key].version)
            for key in COMPONENTS
            if key in self.available_updates
        )
        if versions == self.notified_updates:
            return
        self.notified_updates = versions
        if not versions:
            return
        if self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(
                "Доступно обновление Nodera",
                self.update_list(),
                QSystemTrayIcon.MessageIcon.Information,
                10000,
            )
        if self.isVisible():
            self.show_update_offer()

    def show_update_offer(self):
        if self.closing or not self.available_updates:
            return
        details = [self.update_list()]
        if {"core", "geodata"}.intersection(self.available_updates):
            details.append("Компоненты применятся при следующем подключении.")
        if "app" in self.available_updates:
            details.append("Для новой версии Nodera откроется установщик. Профили сохранятся.")
        description = "\n\n".join(details)
        existing = getattr(self, "update_offer", None)
        if existing is not None:
            existing.message.setText(description)
            existing.action.setText(self.update_apply.text())
            existing.raise_()
            return
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(3000, self.show_update_offer)
            return
        self.reveal()
        dialog = MessageDialog(
            self,
            "Доступно обновление Nodera",
            description,
            self.update_apply.text(),
            cancel=True,
        )
        dialog.cancel_button.setText("Позже")
        dialog.accepted.connect(self.accept_update_offer)

        def finished(_):
            self.update_offer = None
            dialog.deleteLater()

        dialog.finished.connect(finished)
        self.update_offer = dialog
        dialog.open()

    def accept_update_offer(self):
        self.navigate(self.stack.indexOf(self.application_page))
        self.apply_updates()

    def apply_updates(self):
        if self.closing or self.update_busy() or not self.available_updates:
            return
        if self.downloaded_update is not None:
            self.open_update_installer()
            return
        self.set_update_busy(True)
        self.update_status.setText("Загрузка и проверка обновлений…")
        proxy = self.update_proxy()
        component_proxy = proxy or getattr(self, "subscription_proxy", None)
        releases = dict(self.available_updates)

        def activate(manager, release):
            entry = manager.download(release, proxy=component_proxy, cancelled=self.shutdown_event)
            if not self.shutdown_event.is_set():
                manager.activate(entry)

        steps = {
            "core": lambda: activate(self.core_manager, releases["core"]),
            "geodata": lambda: activate(self.geodata_manager, releases["geodata"]),
            "app": lambda: updater.download_release(
                releases["app"], self.store.root, proxy=proxy, cancelled=self.shutdown_event
            ),
        }

        def applied(result):
            completed, errors = result
            self.component_update_notes = list(errors)
            for key in completed.keys() - {"app"}:
                self.available_updates.pop(key, None)
            if {"core", "geodata"}.intersection(completed):
                self.component_update_notes.insert(
                    0, "Компоненты обновлены. Применятся при следующем подключении."
                )
            self.downloaded_update = completed.get("app")
            status = self.component_update_notes.copy()
            if self.downloaded_update:
                status.append("Установщик Nodera загружен. Нажмите «Установить Nodera».")
            self.update_status.setText("\n".join(status))
            if "core" in completed:
                self.refresh_core_version()
            if "geodata" in completed:
                self.geodata_rollback.setVisible(bool(self.geodata_manager.state().get("active")))
            self.set_update_busy(False)

        def failed(message):
            self.update_status.setText(message)
            self.set_update_busy(False)

        self.start_inspection(
            "updates-apply",
            lambda: self.run_update_steps(
                {key: step for key, step in steps.items() if key in releases}
            ),
            applied,
            failed,
            session_bound=False,
        )

    def open_update_installer(self):
        release = self.available_updates["app"]
        try:
            updater.launch_installer(self.downloaded_update, release, resume=self.connected)
        except ClientError as exc:
            self.downloaded_update = None
            status = str(exc)
        else:
            status = (
                "Установщик открыт. После установки нажмите «Открыть Nodera» "
                "для восстановления подключения."
            )
        self.update_status.setText("\n".join([*self.component_update_notes, status]))
        self.set_update_busy(False)

    def rollback_core_update(self):
        self.rollback_update("core", self.core_manager)

    def rollback_geodata_update(self):
        self.rollback_update("geodata", self.geodata_manager)

    def rollback_update(self, key, manager):
        if self.closing or self.update_busy():
            return
        self.set_update_busy(True)

        def done(_):
            if key == "core":
                self.refresh_core_version()
            else:
                self.geodata_rollback.setVisible(bool(self.geodata_manager.state().get("active")))
            self.update_status.setText(
                f"{COMPONENTS[key]} · откат выполнен. Применится при следующем подключении."
            )
            self.set_update_busy(False)

        def failed(_):
            self.update_status.setText(f"{COMPONENTS[key]} · не удалось выполнить откат.")
            self.set_update_busy(False)

        self.start_inspection(
            f"{key}-rollback", manager.rollback, done, failed, session_bound=False
        )
