"""Shared application dialogs with explicit actions and safe dismissal."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import (
    QMessageBox as NativeMessageBox,
)

from nodera.chrome import ShellSurface, TitleBar
from nodera.widgets import label


class AppDialog(QDialog):
    def __init__(self, parent=None, title="Nodera"):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("appModal")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(360)
        self.fit_content = True
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        surface = ShellSurface()
        shell = QVBoxLayout(surface)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        self.title_bar = TitleBar(self, compact=True)
        self.title_bar.minimize.hide()
        self.setWindowTitle(title)
        shell.addWidget(self.title_bar)
        self.body = QWidget()
        self.body.setObjectName("modalBody")
        shell.addWidget(self.body, 1)
        root.addWidget(surface)

    def content_layout(self):
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(12, 6, 12, 10)
        layout.setSpacing(8)
        return layout

    def showEvent(self, event):
        if self.fit_content:
            height = self.layout().totalHeightForWidth(self.width())
            if height > 0:
                self.resize(self.width(), max(self.minimumHeight(), height))
        super().showEvent(event)


class MessageDialog(AppDialog):
    def __init__(self, parent, title, text, action="Понятно", *, destructive=False, cancel=False):
        super().__init__(parent, title)
        self.setFixedWidth(400)
        content = self.content_layout()
        self.message = label(text, "modalDescription", True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.addWidget(self.message)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel_button = None
        if cancel:
            self.cancel_button = QPushButton("Отмена")
            self.cancel_button.clicked.connect(self.reject)
            row.addWidget(self.cancel_button)
            self.cancel_button.setDefault(True)
        self.action = QPushButton(action)
        self.action.setObjectName("dangerAction" if destructive else "primary")
        self.action.clicked.connect(self.accept)
        row.addWidget(self.action)
        content.addLayout(row)
        if cancel:
            self.action.setAutoDefault(False)
            self.cancel_button.setFocus()


def confirm(parent, title, text, action="Продолжить", *, destructive=False):
    return (
        MessageDialog(parent, title, text, action, destructive=destructive, cancel=True).exec()
        == QDialog.DialogCode.Accepted
    )


class QMessageBox:
    """Small compatibility facade so all application messages share the same shell."""

    StandardButton = NativeMessageBox.StandardButton

    @staticmethod
    def warning(parent, title, text, *args, **kwargs):
        MessageDialog(parent, title, text).exec()
        return NativeMessageBox.StandardButton.Ok

    critical = warning
    information = warning

    @staticmethod
    def question(parent, title, text, *args, **kwargs):
        return (
            NativeMessageBox.StandardButton.Yes
            if confirm(parent, title, text)
            else NativeMessageBox.StandardButton.No
        )


def get_text(parent, title, caption, *, text=""):
    dialog = AppDialog(parent, title)
    content = dialog.content_layout()
    edit = QLineEdit(text)
    edit.setMaxLength(80)
    edit.selectAll()
    form = QFormLayout()
    form.addRow(caption, edit)
    content.addLayout(form)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    save = buttons.button(QDialogButtonBox.StandardButton.Save)
    save.setText("Сохранить")
    save.setObjectName("primary")
    save.setEnabled(bool(text.strip()))
    edit.textChanged.connect(lambda value: save.setEnabled(bool(value.strip())))
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    content.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return edit.text(), accepted
