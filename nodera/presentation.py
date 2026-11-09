"""Dashboard and subscription policy presentation, separate from connection operations."""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QStackedLayout,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nodera.controls import ComboBox as QComboBox
from nodera.controls import SubscriptionSelector
from nodera.geolocation import verified_locations
from nodera.profile_view import effective_tun, format_bytes, policy_view, quota_view
from nodera.routes import active_route_views, route_text
from nodera.widgets import (
    AddProfileButton,
    ConnectButton,
    ElidedLabel,
    RouteButton,
    label,
    line_icon,
)


class PresentationMixin:
    def build_connection(self):
        layout = self.page("", map_background=True)
        self.world_map = layout.parentWidget().world_map
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        self.dashboard_header = QFrame()
        self.dashboard_header.setObjectName("dashboardHeader")
        self.dashboard_header.setFixedHeight(38)
        header = QHBoxLayout(self.dashboard_header)
        header.setContentsMargins(8, 3, 8, 3)
        header.setSpacing(8)
        identity = QVBoxLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setSpacing(2)
        self.profile_title = label("Нет профиля", "dashboardProfile")
        self.profile_title.hide()
        self.profile_selector = SubscriptionSelector()
        self.profile_selector.currentIndexChanged.connect(
            lambda: self.switch_profile(self.profile_selector.currentData())
        )
        self.profile_details = label("", "muted")
        self.profile_details.hide()
        identity.addWidget(self.profile_title)
        subscription = QHBoxLayout()
        subscription.setSpacing(6)
        subscription.addWidget(self.profile_selector)
        self.home_pro_badge = label("PRO", "proBadge")
        subscription.addWidget(self.home_pro_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        subscription.addStretch()
        identity.addLayout(subscription)
        self.profile_add = AddProfileButton()
        self.profile_add.setObjectName("addProfile")
        self.profile_add.setAccessibleName("Добавить подписку")
        self.profile_add.setToolTip("Добавить подписку")
        self.profile_add.clicked.connect(self.import_profile)
        identity.addWidget(self.profile_add, alignment=Qt.AlignmentFlag.AlignLeft)
        identity.addWidget(self.profile_details)
        header.addLayout(identity)
        header.addStretch(1)
        self.state = label("VPN отключён" if self.settings.tun else "Прокси отключён", "heroState")
        self.state.setParent(self)
        self.state.setWordWrap(True)
        self.mode_badge = label("", "heroMode")
        self.mode_badge.setParent(self)
        self.mode_badge.hide()
        self.state.hide()
        self.network_tag = QPushButton("Не проверено")
        self.network_tag.setObjectName("connectionStatus")
        self.network_tag.clicked.connect(self.check_route_status)
        self.dashboard_update_button = QPushButton()
        self.dashboard_update_button.setIcon(line_icon("refresh"))
        self.dashboard_update_button.setFixedSize(30, 30)
        self.dashboard_update_button.setAccessibleName("Обновить подписку")
        self.dashboard_update_button.setToolTip("Обновить подписку")
        self.dashboard_update_button.setObjectName("dashboardIcon")
        self.dashboard_update_button.clicked.connect(self.update_profile)
        self.open_profile = QPushButton()
        self.open_profile.setIcon(line_icon("profile"))
        self.open_profile.setFixedSize(30, 30)
        self.open_profile.setAccessibleName("Подписки")
        self.open_profile.setToolTip("Подписки")
        self.open_profile.setObjectName("dashboardIcon")
        self.open_profile.clicked.connect(lambda: self.navigate(1))
        header.addWidget(self.dashboard_update_button)
        self.dashboard_options_button = QPushButton("Опции")
        self.dashboard_options_button.setObjectName("dashboardOptions")
        self.dashboard_options_button.setFixedHeight(30)
        self.dashboard_options_button.clicked.connect(self.channel_options)
        header.addWidget(self.dashboard_options_button)
        header.addWidget(self.open_profile)
        layout.addWidget(self.dashboard_header)

        stage = QWidget()
        stage.setObjectName("connectionCanvas")
        stage.setMinimumHeight(100)
        control_bar = QFrame()
        self.dashboard_dock = control_bar
        control_bar.setObjectName("dashboardDock")
        control_bar.setFixedHeight(158)
        controls = QGridLayout(control_bar)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(22)
        for column in (0, 2):
            controls.setColumnStretch(column, 1)
            controls.setColumnMinimumWidth(column, 180)
        route_container = QWidget()
        self.route_panel = route_container
        route_policy = route_container.sizePolicy()
        route_policy.setRetainSizeWhenHidden(True)
        route_container.setSizePolicy(route_policy)
        route = QVBoxLayout(route_container)
        route.setContentsMargins(0, 0, 0, 0)
        route.setSpacing(6)
        route.addStretch()
        self.route_caption = label("Текущий маршрут", "muted")
        route.addWidget(self.route_caption)
        self.current_route_button = RouteButton("Добавить профиль")
        self.current_route_button.setObjectName("heroRoute")
        self.current_route_button.setMaximumWidth(270)
        self.current_route_button.setIcon(line_icon("servers"))
        self.current_route_button.clicked.connect(lambda: self.navigate(4 if self.profile else 1))
        route.addWidget(self.current_route_button, alignment=Qt.AlignmentFlag.AlignLeft)
        route.addStretch()
        route_container.setParent(self)
        route_container.hide()
        self.power = ConnectButton()
        self.power.setFixedSize(100, 100)
        self.power.setAccessibleName("Подключить или отключить")
        self.power.clicked.connect(self.toggle)
        center = QWidget()
        center.setFixedWidth(240)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)
        self.network_tag.setFixedHeight(24)
        center_layout.addWidget(self.power, 0, Qt.AlignmentFlag.AlignHCenter)
        center_layout.addWidget(self.network_tag, 0, Qt.AlignmentFlag.AlignHCenter)
        controls.addWidget(center, 0, 1, Qt.AlignmentFlag.AlignCenter)
        self.session_panel = QWidget()
        session_policy = self.session_panel.sizePolicy()
        session_policy.setRetainSizeWhenHidden(True)
        self.session_panel.setSizePolicy(session_policy)
        session = QHBoxLayout(self.session_panel)
        session.setContentsMargins(0, 0, 0, 0)
        self.session_time = label("00:00:00", "sessionTime")
        self.session_time.setAlignment(Qt.AlignmentFlag.AlignRight)
        session.addWidget(self.session_time)
        layout.addWidget(stage, 1)
        self.route_summary = label("", "routeSummary", True)
        self.route_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.route_summary.hide()
        layout.addWidget(self.route_summary)
        self.dashboard_feedback = QWidget()
        self.dashboard_feedback.setObjectName("dashboardFeedback")
        self.dashboard_feedback.setFixedSize(232, 20)
        feedback = QStackedLayout(self.dashboard_feedback)
        feedback.setContentsMargins(0, 0, 0, 0)
        feedback.setSpacing(0)
        self.hint = ElidedLabel()
        self.hint.setObjectName("muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setFixedHeight(18)
        feedback.addWidget(self.hint)
        self.subscription_status = ElidedLabel()
        self.subscription_status.setObjectName("muted")
        self.subscription_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subscription_status.setFixedHeight(18)
        self.subscription_status.hide()
        feedback.addWidget(self.subscription_status)
        self.dashboard_access_notice = ElidedLabel()
        self.dashboard_access_notice.setObjectName("subscriptionWarning")
        self.dashboard_access_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dashboard_access_notice.setFixedHeight(18)
        self.dashboard_access_notice.hide()
        feedback.addWidget(self.dashboard_access_notice)
        self.subscription_feedback_timer = QTimer(self)
        self.subscription_feedback_timer.setSingleShot(True)
        self.subscription_feedback_timer.setInterval(8000)
        self.subscription_feedback_timer.timeout.connect(lambda: self.show_subscription_state(None))

        center_layout.addWidget(self.dashboard_feedback, 0, Qt.AlignmentFlag.AlignHCenter)
        plan_panel = QFrame()
        self.dashboard_plan_panel = plan_panel
        plan_panel.setObjectName("dashboardInfoCard")
        plan_panel.setFixedSize(230, 118)
        plan_policy = plan_panel.sizePolicy()
        plan_policy.setRetainSizeWhenHidden(True)
        plan_panel.setSizePolicy(plan_policy)
        plan = QVBoxLayout(plan_panel)
        plan.setContentsMargins(12, 10, 12, 10)
        plan.setSpacing(5)
        plan.addWidget(label("Подписка", "dashboardCaption"))

        rail = QFrame()
        self.quota_panel = rail
        rail.setObjectName("quotaRail")
        row = QVBoxLayout(rail)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.quota_labels = []
        for heading in ("Трафик", "Срок", "Стратегия"):
            cell = QHBoxLayout()
            cell.setContentsMargins(0, 2, 0, 2)
            cell.setSpacing(8)
            cell.addWidget(label(heading, "muted"))
            cell.addStretch()
            value, detail = label("—", "quotaValue"), label("", "railDetail", True)
            detail.hide()
            cell.addWidget(value)
            cell.addWidget(detail)
            self.quota_labels.append((value, detail))
            row.addLayout(cell)
        plan.addWidget(rail)
        plan.addStretch()
        controls.addWidget(
            plan_panel, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        )

        session_block = QFrame()
        session_block.setObjectName("dashboardInfoCard")
        session_block.setFixedSize(230, 118)
        session_layout = QVBoxLayout(session_block)
        session_layout.setContentsMargins(12, 10, 12, 10)
        session_layout.setSpacing(7)
        session_heading = QHBoxLayout()
        session_heading.addWidget(label("Сеанс", "dashboardCaption"))
        session_heading.addStretch()
        session_heading.addWidget(self.session_panel)
        session_layout.addLayout(session_heading)
        self.dashboard_session_empty = label("Нет активного сеанса", "dashboardSummary", True)
        session_layout.addWidget(self.dashboard_session_empty)

        self.traffic_panel = QWidget()
        row = QGridLayout(self.traffic_panel)
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(8)
        row.setVerticalSpacing(5)
        self.stats, self.stat_rates = [], []
        for column, heading in enumerate(("Получено", "Отправлено", "Потоки")):
            row.setColumnStretch(column, 1)
            row.addWidget(label(heading, "dashboardMetricCaption"), 0, column)
            value = label("—", "dashboardMetric")
            row.addWidget(value, 1, column)
            rate = label("", "railDetail")
            row.addWidget(rate, 2, column)
            self.stats.append(value)
            self.stat_rates.append(rate)
        self.stat_rates[2].hide()
        session_layout.addWidget(self.traffic_panel)
        session_layout.addStretch()
        controls.addWidget(
            session_block, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
        )
        layout.addWidget(control_bar)

    def refresh_session_visibility(self):
        self.traffic_panel.setVisible(self.connected)
        self.session_panel.setVisible(self.connected and self.session_tun)
        self.dashboard_session_empty.setVisible(not self.connected)
        self.route_panel.hide()

    def build_routes(self):
        layout = self.page("Правила")
        row = QHBoxLayout()
        self.policy_labels = []
        for heading in ("МАРШРУТЫ", "DNS", "ПРАВИЛА"):
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(label(heading, "eyebrow"))
            value = label("—", "statValue", True)
            detail = label("Профиль ещё не загружен", "muted", True)
            detail.hide()
            column.addWidget(value)
            column.addWidget(detail)
            self.policy_labels.append((value, detail))
            row.addLayout(column, 1)
        layout.addLayout(row)
        self.policy_note = label("", "muted", True)
        self.policy_note.hide()
        layout.addWidget(self.policy_note)
        self.rule_search = QLineEdit()
        self.rule_search.setPlaceholderText("Поиск правила")
        self.rule_search.textChanged.connect(self.filter_rules)
        filters = QHBoxLayout()
        self.rule_origin = QComboBox()
        self.rule_origin.addItems(["Все правила", "Локальные", "Подписка"])
        self.rule_origin.currentIndexChanged.connect(self.filter_rules)
        self.rule_action = QComboBox()
        self.rule_action.addItems(["Все действия", "DIRECT", "REJECT", "Прокси"])
        self.rule_action.currentIndexChanged.connect(self.filter_rules)
        filters.addWidget(self.rule_search, 2)
        filters.addWidget(self.rule_origin)
        filters.addWidget(self.rule_action)
        layout.addLayout(filters)
        self.rule_table = QTableWidget(0, 4)
        self.rule_table.setHorizontalHeaderLabels(["Тип", "Условие", "Действие", "Источник"])
        self.rule_table.verticalHeader().hide()
        self.rule_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rule_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rule_table.setShowGrid(False)
        self.rule_table.setMinimumHeight(180)
        self.rule_table.verticalHeader().setDefaultSectionSize(26)
        self.rule_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.rule_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.rule_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.rule_table, 1)
        actions = QHBoxLayout()
        self.rule_action_buttons = []
        for text, callback in (
            ("Добавить", lambda: self.edit_local_rule()),
            ("Изменить", lambda: self.edit_local_rule(True)),
            ("Удалить", self.delete_local_rule),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            self.rule_action_buttons.append(button)
            actions.addWidget(button)
        self.rules_apply_button = QPushButton("Переподключить")
        self.rules_apply_button.clicked.connect(self.restart_connection)
        actions.addWidget(self.rules_apply_button)
        layout.addLayout(actions)
        self.rules_pending = label(
            "Локальные правила применяются первыми. Правила подписки доступны только для чтения.",
            "muted",
            True,
        )
        layout.addWidget(self.rules_pending)
        self.rule_count = label("", "muted")
        layout.addWidget(self.rule_count)
        self.rule_table.currentCellChanged.connect(lambda *_: self.refresh_rule_controls())

    def refresh_rule_controls(self):
        idle = self.task is None and not self.closing
        local = bool(
            self.profile and 0 <= self.rule_table.currentRow() < len(self.profile.local_rules)
        )
        self.rule_action_buttons[0].setEnabled(idle and self.profile is not None)
        for button in self.rule_action_buttons[1:]:
            button.setEnabled(idle and local)

    def refresh_policy(self):
        view = policy_view(self.profile)
        self.profile_policy = view
        self.rule_table.setRowCount(len(view["rules"]))
        for row, values in enumerate(view["rules"]):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.rule_table.setItem(row, column, item)
            self.rule_table.setItem(
                row,
                3,
                QTableWidgetItem(
                    "Локальное"
                    if self.profile and row < len(self.profile.local_rules)
                    else "Подписка"
                ),
            )
        if self.profile:
            self.policy_labels[0][0].setText(view["routing"])
            self.policy_labels[0][1].setText(
                f"Правил приложений: {view['processes']} · Блокирующих: {view['blocked']}"
            )
            self.policy_labels[1][0].setText(
                "DNS по маршрутам"
                if view["route_dns"]
                else "DNS профиля"
                if view["dns_enabled"]
                else "DNS не включён"
            )
            self.policy_labels[1][1].setText(
                f"{view['dns_mode']} · " + (", ".join(view["resolvers"]) or "Резолверы не заданы")
            )
            self.policy_labels[2][0].setText(str(len(view["rules"])))
            self.policy_labels[2][1].setText(f"Источников правил: {view['providers']}")
            self.policy_note.setText(
                "Транспорты: " + (" · ".join(view["protocols"]) or "—") + " · Из профиля"
            )
        else:
            for value, detail in self.policy_labels:
                value.setText("—")
                detail.setText("Нет профиля")
            self.policy_note.setText("")
        self.filter_rules()
        for value, detail in self.policy_labels:
            value.setToolTip(detail.text())
        self.refresh_rule_controls()

    def filter_rules(self, *_):
        query = self.rule_search.text().casefold().strip()
        visible = 0
        for row in range(self.rule_table.rowCount()):
            text = " ".join(self.rule_table.item(row, col).text() for col in range(3))
            hidden = query not in text.casefold()
            origin = self.rule_origin.currentIndex()
            local = bool(self.profile and row < len(self.profile.local_rules))
            hidden = hidden or (origin == 1 and not local) or (origin == 2 and local)
            target = self.rule_table.item(row, 2).text()
            action = self.rule_action.currentText()
            hidden = (
                hidden
                or (action in {"DIRECT", "REJECT"} and target != action)
                or (action == "Прокси" and target in {"DIRECT", "REJECT", "REJECT-DROP"})
            )
            self.rule_table.setRowHidden(row, hidden)
            visible += not hidden
        self.rule_count.setText(f"{visible} из {self.rule_table.rowCount()}")

    def refresh_dashboard(self):
        self.refresh_subscription_notices()
        self.refresh_session_visibility()
        self.update_network_tag()
        if not self.connected and not self.task:
            self.state.setText(
                ("VPN отключён" if self.settings.tun else "Прокси отключён")
                if self.profile
                else "Нет подписки"
            )
        quota = quota_view(self.profile)
        policy = self.profile_policy
        has_metrics = self.profile is not None and (
            quota["total"] is not None or quota["expiry"] is not None
        )
        self.dashboard_plan_panel.setVisible(has_metrics)
        self.quota_panel.setVisible(has_metrics)
        traffic_detail = "Нет данных"
        if quota["total"] is not None:
            traffic_detail = f"Использовано {format_bytes(quota['used'])}"
            if quota["total"]:
                traffic_detail += f" из {format_bytes(quota['total'])}"
        self.quota_labels[0][0].setText(quota["traffic"] if quota["total"] is not None else "—")
        self.quota_labels[0][1].setText(traffic_detail if self.profile else "")
        self.quota_labels[1][0].setText(quota["days"] if quota["expiry"] is not None else "—")
        try:
            expiry = (
                f"До {datetime.fromtimestamp(quota['expiry']):%d.%m.%Y}" if quota["expiry"] else ""
            )
        except (OverflowError, ValueError, OSError):
            expiry = "Некорректный срок в подписке"
        self.quota_labels[1][1].setText(expiry if self.profile else "")
        self.quota_labels[2][0].setText(policy["strategy"] if self.profile else "—")
        self.quota_labels[2][1].setText(policy["routing"] if self.profile else "")
        if self.profile and self.profile.forced_server:
            self.quota_labels[2][0].setText("Вручную")
            self.quota_labels[2][1].setText("Принудительный маршрут: " + self.profile.forced_server)
        for value, detail in self.quota_labels:
            value.setToolTip(detail.text())
        mode = (
            "TUN"
            if effective_tun(self.profile.config if self.profile else {}, self.settings)
            else "Локальный прокси"
        )
        self.mode_badge.setText(mode)
        self.update_route_pill()
        if quota["expired"] or quota["exhausted"]:
            self.quota_labels[1 if quota["expired"] else 0][1].setText("Обновите подписку")

    def update_route_pill(self):
        self.world_map.set_client_point(self.settings.client_point, self.settings.map_auto_zoom)
        groups = self.groups_data
        routes = active_route_views(self.display_profile(), groups, self.connected)
        locations = verified_locations(routes, self.geo_for_profile())
        display_routes = []
        for index, route in enumerate(routes):
            hops = []
            for step, hop in enumerate(route["hops"]):
                variants = {
                    item["code"]
                    for item in locations
                    if item["route"] == index and item["step"] == step and item["source"] == "geoip"
                }
                hops.append(
                    {
                        **hop,
                        "country": next(iter(variants)) if len(variants) == 1 else "",
                        "source": "geoip",
                    }
                    if variants
                    else hop
                )
            display_routes.append({**route, "hops": hops})
        self.route_summary.setText("\n".join(route_text(route) for route in display_routes))
        self.route_summary.hide()
        self.route_summary.setToolTip(
            "Порядок Relay/CDN не передан подпиской. Его можно уточнить в разделе «Маршрут»."
            if any(not route["order_known"] for route in routes)
            else "Пометка IP — страна проверена по адресу. Остальные страны взяты из профиля или ручной настройки."
        )
        self.world_map.set_locations(locations, self.connected)
        if groups:
            group = groups[0]
            text = group.get("current") if self.connected else None
            if not text:
                text = group["name"] + (
                    " · вручную" if group["type"] == "Selector" else " · автоматически"
                )
            self.current_route_button.setText(text + "  →")
        else:
            self.current_route_button.setText(
                "Выбрать сервер  →" if self.profile else "Добавить профиль  →"
            )
