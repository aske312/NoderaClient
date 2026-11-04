"""Short, task-focused dialogs; subscription links appear only while adding one."""

from PySide6.QtCore import QEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

from nodera.controls import ComboBox as QComboBox
from nodera.locations import map_data
from nodera.models import ClientError
from nodera.server_assets import flag_pixmap
from nodera.subscription import validate_url


class SubscriptionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить подписку")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText("Например, Личная или Рабочая")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…")
        self.url.setClearButtonEnabled(True)
        form = QFormLayout()
        form.addRow("Название", self.name)
        form.addRow("Ссылка", self.url)
        layout.addLayout(form)
        self.message = QLabel("Ссылка понадобится только сейчас. Подписка останется в приложении.")
        self.message.setWordWrap(True)
        self.message.setObjectName("muted")
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Добавить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            validate_url(self.url.text())
        except ClientError as exc:
            self.message.setText(str(exc))
            self.url.setFocus()
            return
        super().accept()


ROLES = {"relay": "Relay / вход", "cdn": "CDN", "server": "Сервер / выход"}


class RouteDialog(QDialog):
    def __init__(self, hops, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Маршрут и страны")
        self.resize(660, 380)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Только отображение на карте. Укажите порядок от входа до выхода; неизвестную страну можно оставить пустой."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Участок", "Страна", "Адрес / IP"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 180)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)
        for hop in hops or [{"role": "server", "country": ""}]:
            self.add_hop(hop)
        row = QHBoxLayout()
        for text, callback in (
            ("Добавить участок", lambda: self.add_hop({"role": "relay", "country": ""})),
            ("Убрать", self.remove_hop),
            ("↑", lambda: self.move_hop(-1)),
            ("↓", lambda: self.move_hop(1)),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Применить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        automatic = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        automatic.setText("Автоматически")
        automatic.clicked.connect(self.reset_route)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.automatic = False

    def add_hop(self, hop):
        if self.table.rowCount() >= 8:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        role = QComboBox()
        for key, text in ROLES.items():
            role.addItem(text, key)
        role.setCurrentIndex(max(0, role.findData(hop.get("role", "server"))))
        place = QComboBox()
        place.addItem("Неизвестно", "")
        place.setItemIcon(0, QIcon(flag_pixmap("xx")))
        for code, data in sorted(map_data()["countries"].items(), key=lambda item: item[1]["name"]):
            place.addItem(data["name"], code)
            place.setItemIcon(place.count() - 1, QIcon(flag_pixmap(code)))
        place.setCurrentIndex(max(0, place.findData(hop.get("country", ""))))
        self.table.setCellWidget(row, 0, role)
        self.table.setCellWidget(row, 1, place)
        address = QLineEdit(str(hop.get("host", "")))
        address.setPlaceholderText("Если известен")
        self.table.setCellWidget(row, 2, address)
        address.installEventFilter(self)
        role.installEventFilter(self)
        place.installEventFilter(self)
        self.table.setRowHeight(row, 43)
        self.table.setCurrentCell(row, 0)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            for row in range(self.table.rowCount()):
                for column in range(3):
                    if self.table.cellWidget(row, column) is watched:
                        self.table.setCurrentCell(row, column)
        return super().eventFilter(watched, event)

    def remove_hop(self):
        if self.table.rowCount() > 1 and self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def move_hop(self, delta):
        row = self.table.currentRow()
        target = row + delta
        if not 0 <= target < self.table.rowCount():
            return
        values = self.route()
        values[row], values[target] = values[target], values[row]
        self.table.setRowCount(0)
        for hop in values:
            self.add_hop(hop)
        self.table.setCurrentCell(target, 0)

    def reset_route(self):
        self.automatic = True
        self.accept()

    def route(self):
        if self.automatic:
            return None
        return [
            dict(
                {
                    "role": self.table.cellWidget(row, 0).currentData(),
                    "country": self.table.cellWidget(row, 1).currentData(),
                },
                **(
                    {"host": self.table.cellWidget(row, 2).text().strip()}
                    if self.table.cellWidget(row, 2).text().strip()
                    else {}
                ),
            )
            for row in range(self.table.rowCount())
        ]
