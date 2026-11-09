"""Scenario controls installed only by the test-build window."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout

from nodera.controls import ComboBox, NumberBox, Switch
from nodera.settings_ui import section, setting
from nodera.test_scenarios import DEMO_PROFILES, PRESETS, STATES, SUBSCRIPTIONS
from nodera.widgets import label


def build_test_mode(window):
    page_index = window.stack.count()
    layout = window.page("Тестовый режим")
    layout.parentWidget().setObjectName("settingsPage")
    window.navigation.add_tool_page(
        page_index, "Тестовый режим", "diagnostics", "События фиктивных подписок"
    )
    window.test_page_index = page_index
    layout.addWidget(
        label(
            "Сценарии применяются только к подпискам «Демо». Реальные подписки работают обычно.",
            "muted",
            True,
        )
    )

    def selector(values):
        box = ComboBox()
        for caption, value in values:
            box.addItem(caption, value)
        box.setFixedWidth(185)
        return box

    window.test_profile = selector(
        (definition["name"], identity) for identity, definition in DEMO_PROFILES.items()
    )
    columns = QHBoxLayout()
    left, right = QVBoxLayout(), QVBoxLayout()
    columns.addLayout(left, 1)
    columns.addLayout(right, 1)
    layout.addLayout(columns)
    form = section(left, "Подписка и сценарий")
    setting(form, "Фиктивная подписка", window.test_profile)
    window.test_preset = selector((caption, value) for value, caption in PRESETS.items())
    setting(form, "Сценарий каналов", window.test_preset)
    window.test_subscription = selector(
        (caption, value) for value, caption in SUBSCRIPTIONS.items()
    )
    setting(form, "Состояние подписки", window.test_subscription)
    row = QHBoxLayout()
    apply = QPushButton("Применить сценарий")
    apply.setObjectName("primary")
    show = QPushButton("Выбрать")
    reset = QPushButton("Сбросить")
    for button in (apply, show, reset):
        row.addWidget(button)
    row.addStretch()
    form.addLayout(row)

    form = section(right, "Отдельный канал")
    window.test_node = selector(())
    window.test_node_state = selector((caption, value) for value, caption in STATES.items())
    window.test_latency = NumberBox()
    window.test_latency.setRange(1, 10000)
    window.test_latency.setSuffix(" мс")
    window.test_latency.setValue(1500)
    window.test_latency.setFixedWidth(185)
    window.test_udp = Switch("Рабочий UDP")
    setting(form, "Канал", window.test_node)
    setting(form, "Состояние", window.test_node_state)
    setting(form, "Задержка", window.test_latency)
    setting(form, "Рабочий UDP", window.test_udp)
    row = QHBoxLayout()
    node_apply = QPushButton("Применить к каналу")
    probe = QPushButton("Шаг проверки")
    row.addWidget(node_apply)
    row.addWidget(probe)
    row.addStretch()
    form.addLayout(row)

    form = section(left, "События по времени")
    window.test_auto = Switch("Автоматические события")
    window.test_interval = NumberBox()
    window.test_interval.setRange(2, 300)
    window.test_interval.setSuffix(" с")
    window.test_interval.setFixedWidth(185)
    setting(form, "Автоматические события", window.test_auto)
    setting(form, "Интервал", window.test_interval)
    left.addWidget(
        label(
            "Отказ основного → задержка → полный отказ → восстановление. «Шаг проверки» ускоряет проверку заморозки после трёх неудач.",
            "muted",
            True,
        )
    )
    row = QHBoxLayout()
    auto_apply = QPushButton("Сохранить")
    advance = QPushButton("Следующее событие")
    row.addWidget(auto_apply)
    row.addWidget(advance)
    row.addStretch()
    form.addLayout(row)
    window.test_status = label("", "muted", True)
    right.addWidget(window.test_status)
    window.test_events = QPlainTextEdit()
    window.test_events.setReadOnly(True)
    window.test_events.setMaximumHeight(110)
    window.test_events.setPlaceholderText("Здесь появятся события сценария")
    right.addWidget(window.test_events)
    left.addStretch()
    right.addStretch()
    layout.addStretch()

    def identity():
        return window.test_profile.currentData()

    def target():
        return next((p for p in window.library.profiles if p.id == identity()), None)

    def refresh_node():
        if not window.test_node.currentData():
            return
        value = window.scenarios.condition(
            identity(), window.test_node.currentData(), window.test_node.currentIndex()
        )
        window.test_node_state.setCurrentIndex(window.test_node_state.findData(value["state"]))
        window.test_latency.setValue(value["delay"] or 1500)
        window.test_udp.setChecked(value["udp"])

    def refresh():
        from nodera.demo import demo_library

        value = window.scenarios.settings(identity())
        window.test_preset.setCurrentIndex(window.test_preset.findData(value["preset"]))
        window.test_subscription.setCurrentIndex(
            window.test_subscription.findData(value["subscription"])
        )
        window.test_auto.setChecked(value["auto"])
        window.test_interval.setValue(value["interval"])
        fixture = next(p for p in demo_library().profiles if p.id == identity())
        window.test_node.blockSignals(True)
        window.test_node.clear()
        for node in fixture.config["proxies"]:
            window.test_node.addItem(node["name"], node["name"])
        window.test_node.blockSignals(False)
        refresh_node()

    def changed(identities):
        for profile_id in identities:
            window.apply_test_scenario(profile_id)
        window.test_events.setPlainText("\n".join(window.scenarios.events))
        window.test_events.verticalScrollBar().setValue(
            window.test_events.verticalScrollBar().maximum()
        )
        active = window.profile and window.profile.id == identity()
        window.test_status.setText(
            "Изменения применены к открытой подписке."
            if active
            else "Сценарий сохранён. Откройте выбранную демоподписку для просмотра."
        )

    def preset_apply():
        window.scenarios.preset(identity(), window.test_preset.currentData())
        window.scenarios.subscription(identity(), window.test_subscription.currentData())
        changed([identity()])
        refresh_node()

    def channel_apply():
        window.scenarios.node(
            identity(),
            window.test_node.currentData(),
            window.test_node_state.currentData(),
            window.test_latency.value(),
            window.test_udp.isChecked(),
        )
        changed([identity()])

    def reset_all():
        window.scenarios.reset(identity())
        changed([identity()])
        refresh()

    def next_event():
        window.scenarios.advance(identity())
        changed([identity()])
        refresh()

    def save_auto():
        window.scenarios.automatic(
            identity(), window.test_auto.isChecked(), window.test_interval.value()
        )
        changed([])

    def step_probe():
        if window.profile and window.profile.id == identity() and window.connected:
            window.test_group()
            window.check_connection()
            window.test_status.setText("Проверка каналов выполнена.")
        else:
            window.test_status.setText("Откройте и подключите выбранную демоподписку.")

    def open_profile():
        profile = target()
        if profile:
            window.switch_profile(profile.id)
        else:
            window.test_status.setText(
                "Демоподписка удалена. Перезапустите стенд, чтобы восстановить её."
            )

    def tick():
        identities = window.scenarios.tick()
        if identities:
            changed(identities)
            refresh()
        if not window.task and "subscription" not in window.inspections:
            for pending in list(window.pending_test_scenarios):
                window.apply_test_scenario(pending)

    apply.clicked.connect(preset_apply)
    node_apply.clicked.connect(channel_apply)
    reset.clicked.connect(reset_all)
    show.clicked.connect(open_profile)
    probe.clicked.connect(step_probe)
    advance.clicked.connect(next_event)
    auto_apply.clicked.connect(save_auto)
    window.test_profile.currentIndexChanged.connect(refresh)
    window.test_node.currentIndexChanged.connect(refresh_node)
    window.test_scenario_timer = QTimer(window)
    window.test_scenario_timer.setInterval(1000)
    window.test_scenario_timer.timeout.connect(tick)
    window.test_scenario_timer.start()
    refresh()


def apply_test_scenario(window, identity):
    from nodera.demo import demo_library

    value = window.scenarios.settings(identity)
    if window.task or "subscription" in window.inspections:
        window.pending_test_scenarios.add(identity)
        return
    window.pending_test_scenarios.discard(identity)
    profile = next((p for p in window.library.profiles if p.id == identity), None)
    if profile is None:
        return
    fixture = next(p for p in demo_library().profiles if p.id == identity)
    updated = window.scenarios.apply_profile(profile)
    window.library = window.library.upsert(updated, activate=False)
    window.store.save_library(window.library)
    if window.profile and window.profile.id == identity:
        window.profile = updated
        window.refresh_profile()
        if window.connected:
            window.test_group()
            window.check_connection()
            window.capture_flows()
    window.log(
        "Сценарий: " + fixture.name + " · " + PRESETS[value["preset"]], source="Тестовый режим"
    )
