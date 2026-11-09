"""Short, task-focused dialogs; subscription links appear only while adding one."""

from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
)

from nodera.modals import AppDialog
from nodera.models import ClientError
from nodera.route_editor import RouteDialog as RouteDialog
from nodera.subscription import validate_url


class SubscriptionDialog(AppDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить подписку")
        self.setMinimumWidth(400)
        layout = self.content_layout()
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
        self.message = QLabel("Подписка сохранится в приложении.")
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
