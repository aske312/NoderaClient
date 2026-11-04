"""Persistent subscription library, independent of a running Mihomo session."""

import time
from dataclasses import replace
from datetime import datetime
from urllib.parse import urlsplit

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from nodera.dialogs import SubscriptionDialog
from nodera.models import ClientError, ProfileLibrary
from nodera.subscription import fetch_profile
from nodera.widgets import label


def subscription_source(url):
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return "Источник не определён"
    return "PRO" if host == "312node.net" or host.endswith(".312node.net") else "Mihomo"


class ProfilesMixin:
    def show_subscription_state(self, state, text=""):
        self.subscription_state = state
        self.subscription_feedback_timer.stop()
        self.power.setSubscriptionStatus(state)
        self.subscription_status.setText(text)
        self.subscription_status.setToolTip(text)
        self.subscription_status.setVisible(bool(text))
        if state in {"success", "error"}:
            self.subscription_feedback_timer.start()

    def display_profile(self):
        session = self.session_profile
        if self.connected and session and self.profile and session.id == self.profile.id:
            return replace(
                session,
                selections=self.profile.selections,
                locations=self.profile.locations,
                route_overrides=self.profile.route_overrides,
                forced_server=self.profile.forced_server,
                config={**session.config, "mode": "global"}
                if self.profile.forced_server
                else session.config,
            )
        return self.profile

    def fetch_subscription(self, url, name):
        proxy = (
            f"http://127.0.0.1:{self.core.mixed_port}"
            if self.connected and not self.session_tun and self.core.mixed_port
            else None
        )
        return fetch_profile(
            url,
            name,
            self.settings.device_id,
            proxy=proxy,
            log=lambda message, level="info": self.core._record(message, level, "Subscription"),
        )

    def build_profile(self):
        layout = self.page("Подписки")
        row = QHBoxLayout()
        row.addWidget(label("Сохранённые профили", "muted"), 1)
        self.import_button = QPushButton("Добавить")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self.import_profile)
        row.addWidget(self.import_button)
        layout.addLayout(row)
        self.profile_list = QListWidget()
        self.profile_list.setObjectName("subscriptionList")
        self.profile_list.setAccessibleName("Сохранённые подписки")
        self.profile_list.currentItemChanged.connect(self.select_profile_item)
        layout.addWidget(self.profile_list, 1)
        self.subscription_info = label("Нет подписок", "muted", True)
        layout.addWidget(self.subscription_info)
        row = QHBoxLayout()
        self.update_button = QPushButton("Обновить")
        self.update_button.clicked.connect(self.update_profile)
        self.rename_profile_button = QPushButton("Переименовать")
        self.rename_profile_button.clicked.connect(self.rename_profile)
        self.delete_button = QPushButton("Удалить")
        self.delete_button.clicked.connect(self.delete_profile)
        for button in (self.update_button, self.rename_profile_button, self.delete_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)

    def refresh_library(self):
        if self.profile:
            self.library = self.library.upsert(self.profile)
        blockers = [QSignalBlocker(self.profile_list), QSignalBlocker(self.profile_selector)]
        ids = [profile.id for profile in self.library.profiles]
        if ids != [
            self.profile_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.profile_list.count())
        ]:
            self.profile_list.clear()
            for profile in self.library.profiles:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, profile.id)
                self.profile_list.addItem(item)
        if ids != [
            self.profile_selector.itemData(index) for index in range(self.profile_selector.count())
        ]:
            self.profile_selector.clear()
            for profile in self.library.profiles:
                self.profile_selector.addItem(profile.name, profile.id)
        for index, profile in enumerate(self.library.profiles):
            count = len(profile.config.get("proxies", []))
            noun = (
                "сервер"
                if count % 10 == 1 and count % 100 != 11
                else "сервера"
                if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14
                else "серверов"
            )
            source = subscription_source(profile.url)
            text = f"{profile.name}\n{source} · {count} {noun}"
            if profile.id == self.library.active_id:
                text += " · Текущая"
            item = self.profile_list.item(index)
            if item.text() != text:
                item.setText(text)
            item.setToolTip(
                profile.name
                + (
                    "\nPRO: источник определён по домену 312node.net"
                    if source == "PRO"
                    else "\nПодписка Mihomo"
                )
            )
            self.profile_selector.setItemText(index, profile.name)
            if profile.id == self.library.active_id:
                self.profile_list.setCurrentItem(item)
                self.profile_selector.setCurrentIndex(index)
        self.profile_selector.setVisible(self.profile is not None)
        self.profile_add.setVisible(self.profile is None)
        self.route_caption.setVisible(self.profile is not None)
        self.current_route_button.setVisible(self.profile is not None)
        del blockers

    def select_profile_item(self, current, previous):
        if current:
            self.switch_profile(current.data(Qt.ItemDataRole.UserRole))

    def switch_profile(self, profile_id):
        if self.task or self.closing or profile_id == self.library.active_id or not profile_id:
            return
        profile = next((item for item in self.library.profiles if item.id == profile_id), None)
        if not profile:
            return
        library = ProfileLibrary(list(self.library.profiles), profile_id)
        connected = self.connected
        self.state.setText("Переключение…")

        def save():
            if connected:
                self.core.stop("subscription switch")
            self.store.save_library(library)

        def saved(_):
            self.library, self.profile = library, profile
            self.set_disconnected()
            self.refresh_profile()
            self.update_retry_at = self.profile_retry_at.get(profile.id, 0)
            self.pending_connect = connected
            self.log("Выбрана подписка «" + profile.name + "».")

        def failed(message):
            if not self.core.running:
                self.set_disconnected()
            self.refresh_library()
            self.error(message)

        self.run_task(save, saved, failed)

    def import_profile(self):
        dialog = SubscriptionDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.load_subscription(dialog.url.text(), dialog.name.text())
        dialog.url.clear()
        dialog.deleteLater()

    def update_profile(self):
        if self.profile:
            self.load_subscription(self.profile.url, self.profile.name)

    def auto_update_profile(self):
        if self.task or self.closing or not self.settings.update_hours:
            return
        if QApplication.activeModalWidget() is not None or "subscription" in self.inspections:
            return
        candidates = ([self.profile] if self.profile else []) + [
            item for item in self.library.profiles if not self.profile or item.id != self.profile.id
        ]
        for profile in candidates:
            retry = (
                self.update_retry_at
                if self.profile and profile.id == self.profile.id
                else self.profile_retry_at.get(profile.id, 0)
            )
            if time.monotonic() < retry:
                continue
            try:
                age = time.time() - datetime.fromisoformat(profile.updated_at).timestamp()
            except (ValueError, OSError, OverflowError):
                age = float("inf")
            if age >= self.settings.update_hours * 3600:
                self.load_subscription(profile.url, profile.name, automatic=True)
                return

    def load_subscription(self, url, name, automatic=False):
        if self.task or self.closing:
            return
        if "subscription" in self.inspections:
            return
        previous = next((item for item in self.library.profiles if item.url == url.strip()), None)
        library = self.library
        activate = not automatic and not self.connected
        self.show_subscription_state(
            "loading", "Обновление подписки…" if previous else "Загрузка подписки…"
        )

        def load():
            from urllib.parse import urlsplit

            profile = self.fetch_subscription(
                url, name.strip() or urlsplit(url).hostname or "Подписка"
            )
            if previous:
                profile = replace(
                    profile,
                    id=previous.id,
                    selections=dict(previous.selections),
                    forced_server=previous.forced_server
                    if any(
                        node.get("name") == previous.forced_server
                        for node in profile.config.get("proxies", [])
                    )
                    else "",
                    locations=dict(previous.locations),
                    route_overrides=dict(previous.route_overrides),
                    local_rules=list(previous.local_rules),
                )
            updated = library.upsert(profile, activate=activate)
            self.core._record("event=subscription_save_started", source="Subscription")
            try:
                self.store.save_library(updated)
            except (OSError, ClientError) as exc:
                self.core._record(
                    f"event=subscription_save_failed type={type(exc).__name__}",
                    "error",
                    "Subscription",
                )
                raise ClientError(
                    "Подписка получена, но сохранить её не удалось. Прежняя подписка сохранена."
                ) from exc
            self.core._record("event=subscription_saved", source="Subscription")
            groups = None
            if self.connected and self.session_profile and self.session_profile.id == profile.id:
                try:
                    groups = self.core.apply_profile(profile)
                except (ClientError, OSError):
                    try:
                        self.store.save_library(library)
                    except (ClientError, OSError) as exc:
                        raise ClientError(
                            "Сеанс сохранён, но не удалось откатить подписку на диске."
                        ) from exc
                    raise
            return updated, profile, groups

        def loaded(result):
            self.show_subscription_state(
                "success", "Подписка обновлена" if previous else "Подписка добавлена"
            )
            self.library, updated, groups = result
            self.profile = self.library.active
            self.profile_retry_at.pop(updated.id, None)
            if self.profile and self.profile.id == updated.id:
                self.update_retry_at = 0
            if groups is not None:
                from copy import deepcopy

                self.session_profile = deepcopy(updated)
                self.delay_results.clear()
                self.set_groups(groups)
                self.rules_pending.setText("")
                self.connection_hint = "Подписка применена без переподключения"
            self.refresh_profile()
            if groups is not None:
                self.hint.setText(self.connection_hint)
                self.log("Подписка обновлена и применена к текущему сеансу.")
            else:
                self.log("Подписка загружена и добавлена в приложение.")
            if previous is None and activate:
                self.navigate(0)

        def failed(message):
            self.show_subscription_state(
                "error",
                "Не удалось обновить подписку" if previous else "Не удалось добавить подписку",
            )
            self.subscription_status.setToolTip(message)
            retry = time.monotonic() + 900
            if previous:
                self.profile_retry_at[previous.id] = retry
            if previous and self.profile and previous.id == self.profile.id:
                self.update_retry_at = retry
            if automatic:
                self.log("Автообновление: " + message)
            else:
                self.error(message)

        self.run_task(load, loaded, failed)

    def rename_profile(self):
        from PySide6.QtWidgets import QInputDialog

        if self.task or not self.profile or self.closing:
            return
        name, accepted = QInputDialog.getText(
            self, "Название подписки", "Название", text=self.profile.name
        )
        if not accepted or not name.strip():
            return
        profile = replace(self.profile, name=name.strip()[:80])
        library = self.library.upsert(profile)

        def saved(_):
            self.library, self.profile = library, profile
            self.refresh_profile()

        self.run_task(lambda: self.store.save_library(library), saved)

    def delete_profile(self):
        if self.task or self.connected or self.closing or not self.profile:
            return
        profiles = [item for item in self.library.profiles if item.id != self.profile.id]
        library = ProfileLibrary(profiles, profiles[0].id if profiles else None)

        def saved(_):
            self.library, self.profile = library, library.active
            self.refresh_profile()
            self.log("Подписка удалена из приложения.")

        self.run_task(lambda: self.store.save_library(library), saved)
