"""Persistent subscription library, independent of a running Mihomo session."""

import threading
import time
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from nodera.dialogs import SubscriptionDialog
from nodera.models import ClientError, ProfileLibrary, visible_proxies
from nodera.profile_view import format_bytes, is_pro_subscription
from nodera.subscription import fetch_profile
from nodera.widgets import AddProfileButton, label


class ProfilesMixin:
    def channel_service(self, profile=None):
        from nodera import channel_selection

        return channel_selection

    def apply_subscription_settings(self, settings):
        if settings == self.settings:
            return
        self.settings = settings
        widgets = {
            self.connection_mode: "tun" if settings.tun else "proxy",
            self.stack_setting: settings.tun_stack,
            self.ipv6_setting: settings.ipv6_mode,
            self.log_level_setting: settings.log_level,
        }
        blockers = [QSignalBlocker(widget) for widget in widgets]
        for widget, value in widgets.items():
            widget.setCurrentIndex(widget.findData(value))
        self.port.setValue(settings.mixed_port)
        self.mtu_setting.setValue(settings.tun_mtu)
        del blockers
        self.refresh_controls()
        self.log("Начальные настройки подключения приняты из подписки Mihomo.")

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
            getattr(self, "subscription_proxy", None)
            if not self.connected or getattr(self.core, "demo", False) is True
            else None
        )
        if self.connected and not self.session_tun and self.core.mixed_port:
            proxy = f"http://127.0.0.1:{self.core.mixed_port}"
        return fetch_profile(
            url,
            name,
            self.settings.device_id,
            proxy=proxy,
            log=lambda message, level="info": self.core._record(message, level, "Subscription"),
        )

    def build_profile(self):
        self.preview_profile_id = None
        self.channel_profile_id = None
        layout = self.page("Подписки")
        row = QHBoxLayout()
        self.library_count = label("", "muted")
        row.addWidget(self.library_count, 1)
        self.import_button = QPushButton("Добавить подписку")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self.import_profile)
        row.addWidget(self.import_button)
        layout.addLayout(row)
        content = QHBoxLayout()
        content.setSpacing(10)
        sidebar = QVBoxLayout()
        sidebar.setSpacing(8)
        self.profile_search = QLineEdit()
        self.profile_search.setPlaceholderText("Найти подписку")
        self.profile_search.setAccessibleName("Поиск подписок")
        self.profile_search.textChanged.connect(self.filter_profiles)
        sidebar.addWidget(self.profile_search)
        self.profile_list = QListWidget()
        self.profile_list.setObjectName("subscriptionList")
        self.profile_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.profile_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.profile_list.setMinimumWidth(210)
        self.profile_list.setMaximumWidth(270)
        self.profile_list.setAccessibleName("Сохранённые подписки")
        self.profile_list.currentItemChanged.connect(self.select_profile_item)
        sidebar.addWidget(self.profile_list, 1)
        content.addLayout(sidebar, 1)
        self.profile_actions = QFrame()
        self.profile_actions.setObjectName("subscriptionDetail")
        detail = QVBoxLayout(self.profile_actions)
        detail.setContentsMargins(14, 14, 14, 14)
        detail.setSpacing(10)
        heading = QHBoxLayout()
        self.profile_summary_title = label("", "subscriptionName", True)
        heading.addWidget(self.profile_summary_title, 1)
        self.profile_pro_badge = label("PRO", "proBadge")
        heading.addWidget(self.profile_pro_badge)
        self.profile_current_badge = label("Активная", "badge")
        heading.addWidget(self.profile_current_badge)
        detail.addLayout(heading)
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(20)
        metrics.setVerticalSpacing(5)
        self.profile_summary_nodes = label("—", "summaryValue")
        self.profile_summary_remaining = label("—", "summaryValue")
        self.profile_summary_expiry = label("—", "summaryValue")
        for column, (caption, value) in enumerate(
            (
                ("Подключения", self.profile_summary_nodes),
                ("Осталось трафика", self.profile_summary_remaining),
                ("Действует до", self.profile_summary_expiry),
            )
        ):
            metrics.addWidget(label(caption, "muted"), 0, column)
            metrics.addWidget(value, 1, column)
        detail.addLayout(metrics)
        self.subscription_info = label("Нет подписок", "muted", True)
        detail.addWidget(self.subscription_info)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.profile_activate_button = QPushButton("Сделать активной")
        self.profile_activate_button.setObjectName("primary")
        self.profile_activate_button.clicked.connect(self.activate_card_profile)
        self.update_button = QPushButton("Обновить")
        self.update_button.clicked.connect(self.update_card_profile)
        self.rename_profile_button = QPushButton("Переименовать")
        self.rename_profile_button.setAccessibleName("Переименовать подписку")
        self.rename_profile_button.clicked.connect(self.rename_profile)
        self.delete_button = QPushButton("Удалить")
        self.delete_button.setObjectName("dangerAction")
        self.delete_button.clicked.connect(self.delete_profile)
        self.profile_options = QPushButton("Опции PRO")
        self.profile_options.clicked.connect(self.card_channel_options)
        for button in (self.profile_activate_button, self.update_button, self.profile_options):
            row.addWidget(button)
        row.addStretch()
        detail.addLayout(row)
        management = QHBoxLayout()
        management.addWidget(self.rename_profile_button)
        management.addStretch()
        management.addWidget(self.delete_button)
        detail.addLayout(management)
        from nodera.channel_options import ProOptionsDialog

        self.pending_channel_result = None
        self.last_channel_result = None
        self.channel_cancel = None
        self.channel_dialog = ProOptionsDialog(self)
        self.channel_status = self.channel_dialog.status
        self.channel_apply = self.channel_dialog.apply
        self.channel_discard = self.channel_dialog.discard
        self.channel_stop = self.channel_dialog.stop
        self.channel_progress.connect(self.channel_status.setText)
        content.addWidget(self.profile_actions, 2, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(content)
        self.profile_empty = QFrame()
        empty = QVBoxLayout(self.profile_empty)
        empty.setSpacing(10)
        empty.addStretch()
        self.empty_profile_add = AddProfileButton()
        self.empty_profile_add.setFixedSize(52, 52)
        self.empty_profile_add.setAccessibleName("Добавить первую подписку")
        self.empty_profile_add.setToolTip("Добавить подписку")
        self.empty_profile_add.clicked.connect(self.import_profile)
        empty.addWidget(self.empty_profile_add, 0, Qt.AlignmentFlag.AlignHCenter)
        empty.addWidget(label("Добавить подписку", "profileName"), 0, Qt.AlignmentFlag.AlignHCenter)
        empty.addStretch()
        layout.addWidget(self.profile_empty, 1)
        layout.addStretch(1)

    def filter_profiles(self):
        query = self.profile_search.text().strip().casefold()
        for index in range(self.profile_list.count()):
            item = self.profile_list.item(index)
            item.setHidden(query not in item.text().casefold())

    def refresh_library(self):
        if self.profile:
            self.library = self.library.upsert(self.profile)
        selectors = (self.profile_selector, self.connections_profile_selector)
        blockers = [QSignalBlocker(widget) for widget in (self.profile_list, *selectors)]
        ids = [profile.id for profile in self.library.profiles]
        if self.preview_profile_id not in ids:
            self.preview_profile_id = self.library.active_id
        if ids != [
            self.profile_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.profile_list.count())
        ]:
            self.profile_list.clear()
            for profile in self.library.profiles:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, profile.id)
                self.profile_list.addItem(item)
        for selector in selectors:
            if ids != [selector.itemData(index) for index in range(selector.count())]:
                selector.clear()
                for profile in self.library.profiles:
                    selector.addItem("", profile.id)
        for index, profile in enumerate(self.library.profiles):
            count = len(visible_proxies(profile.config))
            noun = (
                "подключение"
                if count % 10 == 1 and count % 100 != 11
                else "подключения"
                if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14
                else "подключений"
            )
            text = (
                f"{profile.name}\n"
                + ("PRO · " if is_pro_subscription(profile) else "")
                + f"{count} {noun}"
            )
            if profile.id == self.library.active_id:
                text += " · Активная"
            item = self.profile_list.item(index)
            if item.text() != text:
                item.setText(text)
            item.setToolTip(profile.name)
            for selector in selectors:
                selector.set_profile_name(index, profile.name)
            if profile.id == self.library.active_id:
                for selector in selectors:
                    selector.setCurrentIndex(index)
            if profile.id == self.preview_profile_id:
                self.profile_list.setCurrentItem(item)
        for selector in selectors:
            selector.fit_text()
        self.library_count.setText(f"Сохранено подписок: {len(self.library.profiles)}")
        pro = is_pro_subscription(self.profile)
        for badge in (self.home_pro_badge, self.connections_pro_badge):
            badge.setVisible(pro)
            badge.setToolTip("PRO")
            badge.setAccessibleName("PRO")
        row_height = max(52, self.profile_list.sizeHintForRow(0))
        self.profile_list.setFixedHeight(
            min(6, max(2, len(self.library.profiles))) * row_height + 6
        )
        self.refresh_subscription_card()
        self.profile_selector.setVisible(self.profile is not None)
        self.profile_add.setVisible(self.profile is None)
        has_profiles = bool(self.library.profiles)
        self.profile_list.setVisible(has_profiles)
        self.profile_empty.setVisible(not has_profiles)
        self.profile_actions.setVisible(has_profiles)
        self.import_button.setVisible(has_profiles)
        self.subscription_info.setVisible(has_profiles)
        self.profile_search.setVisible(has_profiles)
        self.filter_profiles()
        del blockers

    def select_profile_item(self, current, previous):
        if current:
            self.preview_profile_id = current.data(Qt.ItemDataRole.UserRole)
            self.refresh_subscription_card()

    def card_profile(self):
        return next(
            (p for p in self.library.profiles if p.id == self.preview_profile_id), self.profile
        )

    def refresh_subscription_card(self):
        profile = self.card_profile()
        current = bool(profile and profile.id == self.library.active_id)
        self.profile_summary_title.setText(profile.name if profile else "Нет подписки")
        self.profile_current_badge.setVisible(current)
        self.profile_pro_badge.setVisible(is_pro_subscription(profile))
        if profile:
            usage = profile.usage
            self.profile_summary_nodes.setText(str(len(visible_proxies(profile.config))))
            total = usage.get("total")
            self.profile_summary_remaining.setText(
                format_bytes(max(0, total - usage.get("upload", 0) - usage.get("download", 0)))
                if total
                else "Без лимита"
                if total == 0
                else "—"
            )
            try:
                expiry = (
                    datetime.fromtimestamp(usage["expire"]).strftime("%d.%m.%Y")
                    if usage.get("expire")
                    else "Без срока"
                    if usage.get("expire") == 0
                    else "—"
                )
            except (ValueError, OverflowError, OSError):
                expiry = "—"
            self.profile_summary_expiry.setText(expiry)
            try:
                date = (
                    datetime.fromisoformat(profile.updated_at).astimezone().strftime("%d.%m, %H:%M")
                )
            except ValueError:
                date = "—"
            info = f"Обновлён: {date}"
            if usage:
                info += "\nИспользовано: " + format_bytes(
                    usage.get("upload", 0) + usage.get("download", 0)
                )
                if total:
                    info += " из " + format_bytes(total)
            self.subscription_info.setText(info)
        else:
            self.subscription_info.setText("Подписка ещё не загружена.")
        self.refresh_card_controls()

    def refresh_card_controls(self):
        profile = self.card_profile()
        current = bool(profile and profile.id == self.library.active_id)
        idle = self.task is None and not self.closing
        pending = bool(self.pending_channel_result or self.channel_cancel)
        self.profile_activate_button.setVisible(bool(profile and not current))
        self.profile_activate_button.setEnabled(idle and not pending)
        self.update_button.setEnabled(bool(idle and profile and not pending))
        self.rename_profile_button.setEnabled(bool(idle and profile and not pending))
        self.delete_button.setEnabled(
            bool(idle and profile and not pending and not (current and self.connected))
        )
        self.profile_options.setVisible(is_pro_subscription(profile))
        own_pending = (
            not self.pending_channel_result
            or profile
            and self.pending_channel_result.profile.id == profile.id
        )
        self.profile_options.setEnabled(bool(idle and profile and own_pending))

    def activate_card_profile(self):
        profile = self.card_profile()
        if profile:
            self.switch_profile(profile.id)

    def update_card_profile(self):
        profile = self.card_profile()
        if profile and not self.pending_channel_result and not self.channel_cancel:
            self.load_subscription(profile.url, profile.name)

    def card_channel_options(self):
        profile = self.card_profile()
        if profile:
            self.channel_options(profile.id)

    def channel_target_profile(self):
        return next(
            (p for p in self.library.profiles if p.id == self.channel_profile_id), self.profile
        )

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
            return self.store.save_library_activation(library, self.settings)

        def saved(result):
            self.library, settings = result
            self.profile = self.library.active
            self.preview_profile_id = self.profile.id
            self.set_disconnected()
            self.apply_subscription_settings(settings)
            self.refresh_profile()
            self.schedule_location_check()
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
        if self.profile and not self.pending_channel_result and not self.channel_cancel:
            self.load_subscription(self.profile.url, self.profile.name)

    def auto_update_profile(self):
        if self.pending_channel_result or self.channel_cancel:
            return
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
        if self.pending_channel_result or self.channel_cancel:
            return
        if self.task or self.closing:
            return
        if "subscription" in self.inspections:
            return
        previous = next((item for item in self.library.profiles if item.url == url.strip()), None)
        library = self.library
        activate = previous is None and not automatic and not self.connected
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
                        for node in visible_proxies(profile.config)
                    )
                    else "",
                    locations=dict(previous.locations),
                    route_overrides=dict(previous.route_overrides),
                    local_rules=list(previous.local_rules),
                    settings_initialized=previous.settings_initialized,
                    channel_capabilities=dict(previous.channel_capabilities),
                )
            else:
                profile = replace(profile, settings_initialized=False)
            updated = library.upsert(profile, activate=activate)
            self.core._record("event=subscription_save_started", source="Subscription")
            try:
                updated, settings = self.store.save_library_activation(updated, self.settings)
                profile = next(item for item in updated.profiles if item.id == profile.id)
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
            return updated, profile, groups, settings

        def loaded(result):
            self.show_subscription_state(
                "success", "Подписка обновлена" if previous else "Подписка добавлена"
            )
            self.library, updated, groups, settings = result
            self.profile = self.library.active
            if previous is None:
                self.preview_profile_id = updated.id
            self.apply_subscription_settings(settings)
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
            self.schedule_location_check(updated)
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

    def refresh_channel_controls(self):
        if not hasattr(self, "profile_options"):
            return
        idle = self.task is None and not self.closing
        target = self.channel_target_profile()
        pro = is_pro_subscription(target)
        pending = self.pending_channel_result
        result = pending or self.last_channel_result
        if result and (not target or result.profile.id != target.id):
            result = None
        own = bool(result and target and result.profile.id == target.id)
        dialog = self.channel_dialog
        self.refresh_card_controls()
        self.dashboard_options_button.setVisible(is_pro_subscription(self.profile))
        self.dashboard_options_button.setEnabled(
            bool(idle and (not pending or self.profile and pending.profile.id == self.profile.id))
        )
        dialog.set_parameters_enabled(idle and not pending)
        dialog.back_button.setEnabled(idle and not pending)
        dialog.start.setEnabled(idle and not pending)
        dialog.start.setVisible(not pending and self.channel_cancel is None)
        self.channel_apply.setVisible(
            bool(
                own
                and pending
                and not result.local_applied
                and result.server_state in {"pending", "accepted"}
            )
        )
        self.channel_discard.setVisible(bool(own and pending and not result.local_applied))
        dialog.recheck.setVisible(bool(own and result.server_state == "unknown"))
        dialog.rollback.setVisible(bool(pro and (not result or own and result.local_applied)))
        for button in (self.channel_apply, self.channel_discard, dialog.recheck, dialog.rollback):
            button.setEnabled(idle)
        self.channel_stop.setVisible(self.channel_cancel is not None)
        if pending or self.channel_cancel:
            self.update_button.setEnabled(False)
            self.dashboard_update_button.setEnabled(False)
            self.delete_button.setEnabled(False)

    def channel_options(self, profile_id=None):
        target = next((p for p in self.library.profiles if p.id == profile_id), self.profile)
        if self.closing or not is_pro_subscription(target):
            return
        if self.pending_channel_result and self.pending_channel_result.profile.id != target.id:
            return
        self.channel_profile_id = target.id
        self.refresh_channel_controls()
        if self.channel_dialog.isVisible():
            self.channel_dialog.raise_()
            return
        if not self.pending_channel_result:
            self.channel_dialog.load_policy(target)
        if self.pending_channel_result or self.channel_cancel:
            self.channel_dialog.show_option("auto_routes")
        else:
            self.channel_dialog.show_overview()
        self.channel_dialog.exec()

    def channel_request_proxy(self):
        proxy = (
            getattr(self, "subscription_proxy", None)
            if not self.connected or getattr(self.core, "demo", False) is True
            else None
        )
        if self.connected and not self.session_tun and self.core.mixed_port:
            proxy = f"http://127.0.0.1:{self.core.mixed_port}"
        return proxy

    def show_channel_result(self, result):
        self.channel_dialog.show_option("auto_routes")
        passed = [row for row in result.rows if row["state"] == "passed"]
        total_bytes = sum(row.get("bytes", 0) for row in passed)
        udp_count = sum(bool(row.get("udp")) for row in passed)
        skipped = sum(row["state"] == "skipped" for row in result.rows)
        lines = [
            f"Проверено каналов: {len(result.rows) - skipped}. Успешно: {len(passed)}. "
            f"Пропущено по настройкам: {skipped}.",
            f"Проверка каждого рабочего канала: не менее {result.duration} с.",
            f"Передано: {total_bytes // 1024} КБ. UDP прошли: {udp_count}.",
        ]
        failed = [row["name"] for row in result.rows if row["state"] == "failed"]
        if failed:
            lines.append("Не прошли: " + ", ".join(failed[:4]) + ("…" if len(failed) > 4 else ""))
        self.channel_dialog.report.setPlainText("\n".join(lines))
        self.channel_dialog.report.setVisible(bool(result.rows))
        messages = {
            "pending": "Профиль подготовлен. Подписка на сервере обновлена. Принять или отказаться?",
            "accepted": "Профиль принят. Доступен откат."
            if result.local_applied
            else "Профиль подготовлен. Подтверждение сервера получено; можно принять в клиенте.",
            "rejected": "Отказ принят. Предыдущая версия подписки восстановлена на сервере.",
            "rolled_back": "Предыдущая версия подписки восстановлена.",
            "expired": "Срок ожидания решения истёк. Сервер вернулся к предыдущей версии.",
            "stale": "Профиль изменился на сервере. Выполните подбор заново.",
            "unknown": "Результат команды не подтверждён. Проверьте состояние; автообновление приостановлено.",
        }
        self.channel_status.setText(messages.get(result.server_state, "Проверка завершена."))
        if result.local_applied and result.server_state in {
            "rolled_back",
            "rejected",
            "expired",
            "stale",
        }:
            self.channel_status.setText(
                "Состояние сервера проверено. Нажмите «Откатить подписку», чтобы восстановить профиль клиента."
            )
        self.refresh_channel_controls()

    def receive_channel_result(self, result):
        self.channel_cancel = None
        if (
            result.server_state in {"pending", "unknown"}
            or (result.server_state == "accepted" and not result.local_applied)
            or (
                result.local_applied
                and result.server_state in {"rolled_back", "rejected", "expired", "stale"}
            )
        ):
            self.pending_channel_result = result
        else:
            self.pending_channel_result = None
        self.last_channel_result = result
        self.show_channel_result(result)

    def start_channel_selection(self, duration=60, require_udp=False, policy=None):
        from copy import deepcopy
        from dataclasses import asdict

        from nodera.channel_policy import ChannelPolicy

        target = self.channel_target_profile()
        service = self.channel_service(target)
        change_server_selection, select_channels = (
            service.change_server_selection,
            service.select_channels,
        )

        if self.task or self.closing or not target or self.pending_channel_result:
            return
        if "subscription" in self.inspections:
            self.channel_status.setText("Дождитесь завершения обновления подписки.")
            return
        policy = (policy or ChannelPolicy()).validate()
        preferences = {**self.settings.channel_preferences, target.id: asdict(policy)}
        settings = replace(self.settings, channel_preferences=preferences)
        try:
            self.store.save_settings(settings)
        except (ClientError, OSError):
            self.channel_status.setText("Не удалось сохранить параметры подбора. Повторите запуск.")
            return
        self.settings = settings
        profile = deepcopy(target)
        proxy = self.channel_request_proxy()
        device_id = self.settings.device_id
        cancel = self.channel_cancel = threading.Event()
        self.channel_dialog.report.hide()
        self.channel_status.setText("Подготовка проверки каналов…")
        progress = self.channel_progress.emit

        def prepare():
            from nodera.channel_selection import check_cancel

            result = select_channels(
                profile,
                device_id,
                self.store.root / "channel-checks",
                duration=duration,
                require_udp=require_udp,
                policy=policy,
                proxy=proxy,
                cancel=cancel,
                progress=progress,
            )
            check_cancel(cancel)
            progress("Обновление подписки на сервере и сохранение версии для отката…")
            result = change_server_selection(result, device_id, proxy=proxy)
            if cancel.is_set() and result.server_state in {"pending", "accepted"}:
                result = change_server_selection(result, device_id, "reject", proxy=proxy)
            return result

        def failed(message):
            self.channel_cancel = None
            self.channel_status.setText(message)
            self.refresh_channel_controls()

        self.run_task(prepare, self.receive_channel_result, failed)

    def recheck_ready_channels(self):
        read_server_selection = self.channel_service(
            self.channel_target_profile()
        ).read_server_selection

        result = self.pending_channel_result or self.last_channel_result
        if self.task or not result:
            return
        proxy = self.channel_request_proxy()
        self.run_task(
            lambda: read_server_selection(result, self.settings.device_id, proxy=proxy),
            self.receive_channel_result,
            self.channel_status.setText,
        )

    def discard_ready_channels(self):
        change_server_selection = self.channel_service(
            self.channel_target_profile()
        ).change_server_selection

        result = self.pending_channel_result
        if self.task or not result:
            return
        proxy = self.channel_request_proxy()
        self.run_task(
            lambda: change_server_selection(result, self.settings.device_id, "reject", proxy=proxy),
            self.receive_channel_result,
            self.channel_status.setText,
        )

    def apply_ready_channels(self):
        from copy import deepcopy

        target = self.channel_target_profile()
        service = self.channel_service(target)
        apply_selection, change_server_selection = (
            service.apply_selection,
            service.change_server_selection,
        )

        result = self.pending_channel_result
        if (
            self.task
            or self.closing
            or not result
            or not target
            or result.profile.id != target.id
            or result.server_state not in {"pending", "accepted"}
        ):
            return
        previous = deepcopy(target)
        result = deepcopy(result)
        result.previous_profile = previous
        result.profile = replace(
            result.profile,
            name=previous.name,
            local_rules=list(previous.local_rules),
            locations=dict(previous.locations),
            route_overrides=deepcopy(previous.route_overrides),
            settings_initialized=previous.settings_initialized,
        )
        connected = bool(
            self.connected and self.session_profile and self.session_profile.id == previous.id
        )
        rollback = deepcopy(self.session_profile) if connected else previous
        if connected:
            rollback.selections = dict(previous.selections)
            rollback.forced_server = previous.forced_server
        library = self.library
        proxy = self.channel_request_proxy()
        device_id = self.settings.device_id

        def apply():
            accepted = change_server_selection(result, device_id, "accept", proxy=proxy)
            if accepted.server_state != "accepted":
                return accepted, None, None
            try:
                value = apply_selection(
                    accepted, rollback, library, self.store, self.core, connected
                )
            except ClientError as exc:
                try:
                    accepted = change_server_selection(accepted, device_id, "rollback", proxy=proxy)
                except ClientError:
                    accepted.server_state = "unknown"
                return accepted, None, str(exc)
            accepted.local_applied = True
            return accepted, value, None

        def applied(value):
            accepted, saved, error = value
            if saved:
                self.library, groups = saved
                self.profile = self.library.active
                if groups is not None:
                    self.session_profile = deepcopy(self.profile)
                    self.delay_results.clear()
                    self.set_groups(groups)
                self.refresh_profile()
                self.schedule_location_check()
            self.receive_channel_result(accepted)
            if error:
                self.channel_status.setText(error + " " + self.channel_status.text())

        self.run_task(apply, applied, self.channel_status.setText)

    def rollback_ready_channels(self):
        from copy import deepcopy

        target = self.channel_target_profile()
        service = self.channel_service(target)
        apply_selection = service.apply_selection
        change_server_selection = service.change_server_selection
        discover_server_selection = service.discover_server_selection

        result = self.pending_channel_result or self.last_channel_result
        if result and target and result.profile.id != target.id:
            result = None
        if (
            self.task
            or not target
            or result
            and (
                not result.local_applied
                or not result.previous_profile
                or target.id != result.profile.id
            )
        ):
            return
        result = deepcopy(result)
        current = deepcopy(target)
        connected = bool(
            self.connected and self.session_profile and self.session_profile.id == current.id
        )
        library = self.library
        proxy = self.channel_request_proxy()

        def rollback():
            nonlocal result
            if result is None:
                result = discover_server_selection(current, self.settings.device_id, proxy=proxy)
            restored = change_server_selection(
                result, self.settings.device_id, "rollback", proxy=proxy
            )
            if restored.server_state not in {"rolled_back", "rejected", "expired"}:
                return restored, None
            old = replace(restored.previous_profile, config=restored.restored_config)
            reverse = replace(restored, profile=old, created_at=time.time(), require_udp=False)
            value = apply_selection(reverse, current, library, self.store, self.core, connected)
            restored.local_applied = False
            return restored, value

        def rolled_back(value):
            restored, saved = value
            if saved:
                self.library, groups = saved
                self.profile = self.library.active
                if groups is not None:
                    self.session_profile = deepcopy(self.profile)
                    self.set_groups(groups)
                self.refresh_profile()
            self.receive_channel_result(restored)

        def failed(message):
            if result is not None:
                result.server_state = "unknown"
                self.receive_channel_result(result)
            self.channel_status.setText(message)

        self.run_task(rollback, rolled_back, failed)

    def rename_profile(self):
        from PySide6.QtWidgets import QInputDialog

        target = self.card_profile()
        if self.task or not target or self.closing:
            return
        name, accepted = QInputDialog.getText(
            self, "Название подписки", "Название", text=target.name
        )
        if not accepted or not name.strip():
            return
        profile = replace(target, name=name.strip()[:80])
        library = self.library.upsert(profile, activate=False)

        def saved(_):
            self.library = library
            self.profile = library.active
            self.refresh_profile()

        self.run_task(lambda: self.store.save_library(library), saved)

    def delete_profile(self):
        target = self.card_profile()
        if (
            self.task
            or self.closing
            or not target
            or self.connected
            and target.id == self.library.active_id
        ):
            return
        profiles = [item for item in self.library.profiles if item.id != target.id]
        active_id = (
            self.library.active_id
            if any(p.id == self.library.active_id for p in profiles)
            else profiles[0].id
            if profiles
            else None
        )
        library = ProfileLibrary(profiles, active_id)

        def saved(result):
            self.library, settings = result
            self.profile = self.library.active
            self.apply_subscription_settings(settings)
            self.refresh_profile()
            self.log("Подписка удалена из приложения.")

        self.run_task(lambda: self.store.save_library_activation(library, self.settings), saved)
