"""PRO options hub with independent pages for subscription commands."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLayout,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from nodera.channel_policy import PROTOCOL_LABELS, ChannelPolicy, channel_capabilities
from nodera.chrome import ShellSurface, TitleBar
from nodera.controls import ComboBox, NumberBox, Switch
from nodera.settings_ui import section, setting
from nodera.widgets import label


class OptionPages(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._current = None

    def addWidget(self, page):
        self.layout().addWidget(page)
        page.hide()

    def currentWidget(self):
        return self._current

    def setCurrentWidget(self, page):
        if self._current is not None:
            self._current.hide()
        self._current = page
        page.show()
        self.updateGeometry()


class ProOptionsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("proOptions")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        surface = ShellSurface()
        surface.setFixedWidth(480)
        shell = QVBoxLayout(surface)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        self.title_bar = TitleBar(self)
        self.title_bar.minimize.hide()
        self.setWindowTitle("Опции PRO")
        shell.addWidget(self.title_bar)
        body = QWidget()
        body.setObjectName("settingsPage")
        content = QVBoxLayout(body)
        content.setContentsMargins(16, 4, 16, 16)
        self.pages = OptionPages()
        content.addWidget(self.pages)
        shell.addWidget(body)
        root.addWidget(surface)

        self.options = {}
        self.overview = QWidget()
        overview = QVBoxLayout(self.overview)
        overview.setContentsMargins(0, 0, 0, 0)
        overview.setSpacing(12)
        self.subscription_caption = label("Mihomo", "muted", True)
        overview.addWidget(self.subscription_caption)
        self.option_list = QVBoxLayout()
        self.option_list.setSpacing(8)
        overview.addLayout(self.option_list)
        close_overview = QPushButton("Закрыть")
        close_overview.clicked.connect(self.reject)
        overview.addWidget(close_overview, 0, Qt.AlignmentFlag.AlignRight)
        self.pages.addWidget(self.overview)

        self.auto_routes = QWidget()
        layout = QVBoxLayout(self.auto_routes)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.back_button = QPushButton("‹ Все опции")
        self.back_button.setObjectName("quietAction")
        self.back_button.clicked.connect(self.show_overview)
        layout.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignLeft)
        form = section(layout, "Авто-маршруты")
        self.channel_count = NumberBox()
        self.channel_count.setRange(0, 64)
        self.channel_count.setSpecialValueText("Все доступные")
        self.channel_count.setFixedWidth(190)
        setting(form, "Каналы · максимум", self.channel_count)

        self.protocol_picker = QPushButton("Все протоколы  ▾")
        self.protocol_picker.setObjectName("protocolPicker")
        self.protocol_picker.setFixedWidth(190)
        self.protocol_menu = QMenu(self)
        panel = QWidget()
        choices = QVBoxLayout(panel)
        choices.setContentsMargins(8, 6, 8, 6)
        choices.setSpacing(8)
        self.all_protocols = QCheckBox("Все протоколы")
        self.all_protocols.setChecked(True)
        choices.addWidget(self.all_protocols)
        self.protocols = QListWidget()
        self.protocols.setObjectName("protocolChoices")
        self.protocols.setFixedSize(396, 156)
        self.protocols.setFlow(QListView.Flow.LeftToRight)
        self.protocols.setWrapping(True)
        self.protocols.setResizeMode(QListView.ResizeMode.Adjust)
        self.protocols.setGridSize(QSize(124, 28))
        self.protocols.setUniformItemSizes(True)
        self.protocols.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.protocols.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.all_protocols.toggled.connect(self.update_protocols)
        self.protocols.itemChanged.connect(self.update_protocol_summary)
        self.protocols.setEnabled(False)
        choices.addWidget(self.protocols)
        done = QPushButton("Готово")
        done.clicked.connect(self.protocol_menu.hide)
        choices.addWidget(done, 0, Qt.AlignmentFlag.AlignRight)
        action = QWidgetAction(self.protocol_menu)
        action.setDefaultWidget(panel)
        self.protocol_menu.addAction(action)
        self.protocol_picker.setMenu(self.protocol_menu)
        setting(form, "Протоколы", self.protocol_picker)

        self.network = ComboBox()
        self.network.addItem("Любой", "")
        self.network.setFixedWidth(190)
        setting(form, "Транспорт", self.network)
        self.duration = ComboBox()
        for seconds in (30, 60, 120):
            self.duration.addItem(f"{seconds} с / канал", seconds)
        self.duration.setCurrentIndex(1)
        self.duration.setFixedWidth(190)
        setting(form, "Проверка", self.duration)

        checks = QHBoxLayout()
        checks.setContentsMargins(12, 4, 12, 2)
        checks.setSpacing(8)
        self.tls = Switch("Только TLS")
        self.udp = Switch("Рабочий UDP")
        for caption, switch in (("Только TLS", self.tls), ("Рабочий UDP", self.udp)):
            checks.addWidget(label(caption))
            checks.addWidget(switch)
            checks.addStretch()
        form.addLayout(checks)
        layout.addWidget(label("Проверка расходует трафик. Доступен откат.", "muted"))
        self.status = label("", "muted", True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFixedHeight(100)
        self.report.hide()
        layout.addWidget(self.report)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        self.rollback = QPushButton("Откатить")
        self.rollback.setObjectName("quietAction")
        self.rollback.clicked.connect(owner.rollback_ready_channels)
        row.addWidget(self.rollback)
        row.addStretch()
        self.apply = QPushButton("Принять")
        self.apply.setObjectName("primary")
        self.apply.clicked.connect(owner.apply_ready_channels)
        self.discard = QPushButton("Отказаться")
        self.discard.clicked.connect(owner.discard_ready_channels)
        self.recheck = QPushButton("Проверить статус")
        self.recheck.clicked.connect(owner.recheck_ready_channels)
        self.stop = QPushButton("Отменить")
        self.stop.clicked.connect(lambda: owner.channel_cancel and owner.channel_cancel.set())
        for button in (self.discard, self.recheck, self.apply, self.stop):
            button.hide()
            row.addWidget(button)
        self.rollback.hide()
        self.start = QPushButton("Подобрать маршруты")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self.start_selection)
        row.addWidget(self.start)
        layout.addLayout(row)
        self.add_option(
            "auto_routes", "Авто-маршруты", "Подбор и проверка каналов Mihomo", self.auto_routes
        )
        profile = getattr(owner, "profile", None)
        if profile is not None:
            self.load_policy(profile)
        self.show_overview()

    def add_option(self, key, title, summary, page):
        self.options[key] = page
        self.pages.addWidget(page)
        button = QPushButton()
        button.setObjectName("proOptionCard")
        button.setFixedHeight(72)
        button.setAccessibleName(title)
        row = QHBoxLayout(button)
        row.setContentsMargins(14, 12, 14, 12)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(title, "profileName"))
        text.addWidget(label(summary, "muted"))
        row.addLayout(text, 1)
        row.addWidget(label("›", "profileName"))
        for child in button.findChildren(type(self.subscription_caption)):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        button.clicked.connect(lambda checked=False: self.show_option(key))
        self.option_list.addWidget(button)

    def show_option(self, key):
        self.pages.setCurrentWidget(self.options[key])
        self.fit_page()

    def show_overview(self):
        self.pages.setCurrentWidget(self.overview)
        self.fit_page()

    def fit_page(self):
        widget = self.pages
        while widget is not None:
            if widget.layout():
                widget.layout().invalidate()
            widget.updateGeometry()
            if widget is self:
                break
            widget = widget.parentWidget()
        self.layout().activate()
        self.adjustSize()

    def update_protocol_summary(self):
        selected = [
            self.protocols.item(index).text()
            for index in range(self.protocols.count())
            if self.protocols.item(index).checkState() == Qt.CheckState.Checked
        ]
        text = (
            "Все протоколы"
            if self.all_protocols.isChecked()
            else (
                "Выбрать"
                if not selected
                else ", ".join(selected)
                if len(selected) <= 2
                else f"Выбрано: {len(selected)}"
            )
        )
        self.protocol_picker.setText(text + "  ▾")

    def update_protocols(self):
        self.protocols.setEnabled(
            self.all_protocols.isEnabled() and not self.all_protocols.isChecked()
        )
        self.update_protocol_summary()

    def set_parameters_enabled(self, enabled):
        for widget in (
            self.duration,
            self.udp,
            self.channel_count,
            self.all_protocols,
            self.network,
            self.tls,
        ):
            widget.setEnabled(enabled)
        self.protocol_picker.setEnabled(enabled)
        self.update_protocols()

    def load_policy(self, profile):
        available = channel_capabilities(profile.config, profile.channel_capabilities)
        self.subscription_caption.setText(profile.name + " · Mihomo")
        policy = ChannelPolicy(**self.owner.settings.channel_preferences.get(profile.id, {}))
        self.channel_count.setValue(policy.max_channels)
        self.all_protocols.setChecked(not policy.protocols)
        self.protocols.blockSignals(True)
        self.protocols.clear()
        for protocol in available["protocols"]:
            item = QListWidgetItem(PROTOCOL_LABELS[protocol], self.protocols)
            item.setData(Qt.ItemDataRole.UserRole, protocol)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setSizeHint(QSize(124, 28))
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) in policy.protocols
                else Qt.CheckState.Unchecked
            )
        self.protocols.blockSignals(False)
        self.protocols.setFixedHeight(max(36, ((self.protocols.count() + 2) // 3) * 28 + 8))
        self.network.clear()
        self.network.addItem("Любой", "")
        for network in available["networks"]:
            self.network.addItem(network.upper(), network)
        self.network.setCurrentIndex(max(0, self.network.findData(policy.network)))
        self.tls.setChecked(policy.require_tls)
        self.update_protocol_summary()
        if set(policy.protocols) - set(available["protocols"]):
            self.status.setText("Состав протоколов изменился. Выберите доступные протоколы.")

    def start_selection(self):
        self.show_option("auto_routes")
        protocols = [
            self.protocols.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.protocols.count())
            if self.protocols.item(index).checkState() == Qt.CheckState.Checked
        ]
        if not self.all_protocols.isChecked() and not protocols:
            self.status.setText("Выберите хотя бы один протокол или включите все доступные.")
            return
        policy = ChannelPolicy(
            self.channel_count.value(),
            [] if self.all_protocols.isChecked() else protocols,
            self.network.currentData(),
            self.tls.isChecked(),
        )
        self.owner.start_channel_selection(
            self.duration.currentData(), self.udp.isChecked(), policy
        )

    def reject(self):
        if self.owner.channel_cancel:
            self.owner.channel_cancel.set()
            self.status.setText("Отмена проверки…")
            return
        if self.owner.task:
            return
        super().reject()


# Existing callers may still use the name of the first PRO option.
ChannelOptionsDialog = ProOptionsDialog
