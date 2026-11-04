import ipaddress
import re

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from nodera.controls import ComboBox as QComboBox
from nodera.models import ClientError


def make_rule(kind, condition, target):
    condition = condition.strip()
    if (
        kind not in {"DOMAIN", "DOMAIN-SUFFIX", "IP-CIDR", "IP-CIDR6", "PROCESS-NAME"}
        or not condition
        or any(char in condition + target for char in ",\r\n")
    ):
        raise ClientError("Укажите корректное условие и действие без запятых и переносов строк.")
    if kind.startswith("IP-CIDR"):
        try:
            network = ipaddress.ip_network(condition, strict=False)
            if (kind == "IP-CIDR6") != (network.version == 6):
                raise ValueError
            condition = str(network)
        except ValueError as exc:
            raise ClientError("Укажите подсеть CIDR, например 192.168.0.0/16.") from exc
    elif kind.startswith("DOMAIN"):
        try:
            condition = condition.rstrip(".").encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ClientError("Некорректное доменное имя.") from exc
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,253}", condition):
            raise ClientError("Введите только домен, без https:// и пути.")
    elif len(condition) > 255 or any(char in condition for char in "\\/"):
        raise ClientError("Укажите имя процесса, например browser.exe, без полного пути.")
    return f"{kind},{condition},{target}" + (",no-resolve" if kind.startswith("IP-CIDR") else "")


class RuleDialog(QDialog):
    def __init__(self, profile, rule=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Локальное правило")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItems(["DOMAIN-SUFFIX", "DOMAIN", "IP-CIDR", "IP-CIDR6", "PROCESS-NAME"])
        self.condition = QLineEdit()
        self.condition.setPlaceholderText("example.com")
        self.target = QComboBox()
        targets = [
            "DIRECT",
            "REJECT",
            *[item["name"] for item in profile.config.get("proxy-groups", [])],
            *[item["name"] for item in profile.config.get("proxies", [])],
        ]
        self.target.addItems(list(dict.fromkeys(value for value in targets if "," not in value)))
        form.addRow("Тип", self.kind)
        form.addRow("Условие", self.condition)
        form.addRow("Действие", self.target)
        layout.addLayout(form)
        self.message = QLabel("Локальные правила применяются перед правилами подписки.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        if rule:
            parts = rule.split(",")
            self.kind.setCurrentText(parts[0])
            self.condition.setText(parts[1])
            self.target.setCurrentText(parts[2])
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            self.value = make_rule(
                self.kind.currentText(), self.condition.text(), self.target.currentText()
            )
        except ClientError as exc:
            self.message.setText(str(exc))
            return
        super().accept()
