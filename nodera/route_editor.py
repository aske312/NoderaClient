"""Ordered map points, edited as individual compact rows."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nodera.controls import ComboBox
from nodera.geolocation import valid_host
from nodera.locations import map_data
from nodera.modals import AppDialog
from nodera.server_assets import flag_pixmap
from nodera.widgets import label


class HopRow(QFrame):
    def __init__(self, hop, editor):
        super().__init__()
        self.setObjectName("routeHop")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.number = label("", "hopNumber")
        self.number.setFixedSize(24, 24)
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.number, 0, Qt.AlignmentFlag.AlignTop)
        fields = QVBoxLayout()
        fields.setSpacing(4)
        row = QHBoxLayout()
        self.role = ComboBox()
        for key, text in (("relay", "Вход / Relay"), ("cdn", "CDN"), ("server", "Выход")):
            self.role.addItem(text, key)
        self.role.setCurrentIndex(max(0, self.role.findData(hop.get("role", "server"))))
        self.role.setAccessibleName("Тип участка")
        self.place = ComboBox()
        self.place.addItem("Страна не указана", "")
        self.place.setItemIcon(0, QIcon(flag_pixmap("xx")))
        for code, data in sorted(map_data()["countries"].items(), key=lambda item: item[1]["name"]):
            self.place.addItem(data["name"], code)
            self.place.setItemIcon(self.place.count() - 1, QIcon(flag_pixmap(code)))
        self.place.setCurrentIndex(max(0, self.place.findData(hop.get("country", ""))))
        self.place.setAccessibleName("Страна участка")
        row.addWidget(self.role, 1)
        row.addWidget(self.place, 2)
        fields.addLayout(row)
        self.host = QLineEdit(hop.get("host", ""))
        self.host.setPlaceholderText("Домен или IP · необязательно")
        self.host.setAccessibleName("Адрес участка")
        self.host.setClearButtonEnabled(True)
        fields.addWidget(self.host)
        layout.addLayout(fields, 1)
        controls = QVBoxLayout()
        controls.setSpacing(3)
        self.up, self.down, self.remove = QToolButton(), QToolButton(), QToolButton()
        self.up.setArrowType(Qt.ArrowType.UpArrow)
        self.down.setArrowType(Qt.ArrowType.DownArrow)
        self.remove.setText("×")
        for button, title, callback in (
            (self.up, "Переместить выше", lambda: editor.move_hop(self, -1)),
            (self.down, "Переместить ниже", lambda: editor.move_hop(self, 1)),
            (self.remove, "Удалить участок", lambda: editor.remove_hop(self)),
        ):
            button.setObjectName("hopAction")
            button.setFixedSize(22, 19)
            button.setIconSize(QSize(12, 12))
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)

    def value(self):
        result = {"role": self.role.currentData(), "country": self.place.currentData()}
        if self.host.text().strip():
            result["host"] = self.host.text().strip()
        return result


class RouteDialog(AppDialog):
    def __init__(self, hops, parent=None, node_name=""):
        super().__init__(parent)
        self.fit_content = False
        self.setWindowTitle("Маршрут на карте")
        self.resize(600, min(468, 130 + 84 * len(hops or [{}])))
        self.setMinimumSize(540, 220)
        self.automatic = False
        self.hop_rows = []
        layout = self.content_layout()
        layout.setSpacing(8)
        if node_name:
            layout.addWidget(label(node_name, "profileName", True))
        layout.addWidget(label("Участки от входа к выходу.", "muted", True))
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        self.rows_layout = QVBoxLayout(container)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(6)
        self.rows_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        self.add_button = QPushButton("+ Добавить участок")
        self.add_button.setObjectName("quietAction")
        self.add_button.clicked.connect(lambda: self.add_hop({"role": "relay", "country": ""}))
        self.message = label("", "routeEditorError", True)
        self.message.hide()
        layout.addWidget(self.message)
        footer = QHBoxLayout()
        footer.addWidget(self.add_button)
        reset = QPushButton("Из подписки")
        reset.setObjectName("quietAction")
        reset.setToolTip("Убрать ручные изменения маршрута")
        reset.clicked.connect(self.reset_route)
        footer.addWidget(reset)
        footer.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        for hop in hops or [{"role": "server", "country": ""}]:
            self.add_hop(hop)

    def renumber(self):
        for index, row in enumerate(self.hop_rows):
            row.number.setText(str(index + 1))
            row.up.setEnabled(index > 0)
            row.down.setEnabled(index + 1 < len(self.hop_rows))
            row.remove.setEnabled(len(self.hop_rows) > 1)
        self.add_button.setEnabled(len(self.hop_rows) < 8)

    def add_hop(self, hop):
        if len(self.hop_rows) >= 8:
            return
        row = HopRow(hop, self)
        self.hop_rows.append(row)
        self.rows_layout.insertWidget(len(self.hop_rows) - 1, row)
        self.renumber()
        if self.isVisible():
            self.rows_layout.activate()
            self.scroll.ensureWidgetVisible(row)
            row.host.setFocus()

    def remove_hop(self, row):
        if len(self.hop_rows) <= 1:
            return
        self.hop_rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.renumber()

    def move_hop(self, row, delta):
        index = self.hop_rows.index(row)
        target = index + delta
        if not 0 <= target < len(self.hop_rows):
            return
        self.hop_rows[index], self.hop_rows[target] = self.hop_rows[target], row
        self.rows_layout.removeWidget(row)
        self.rows_layout.insertWidget(target, row)
        self.renumber()

    def reset_route(self):
        self.automatic = True
        self.accept()

    def route(self):
        return None if self.automatic else [row.value() for row in self.hop_rows]

    def accept(self):
        if not self.automatic:
            for index, row in enumerate(self.hop_rows):
                host = row.host.text().strip()
                if host and not valid_host(host):
                    self.message.setText(
                        f"Участок {index + 1}: укажите домен или IP без ссылки и номера порта."
                    )
                    self.message.show()
                    row.host.setFocus()
                    return
        super().accept()
