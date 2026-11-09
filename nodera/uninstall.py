"""Interactive removal choices; upgrades always preserve user data."""

import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from nodera.modals import AppDialog, QMessageBox, confirm
from nodera.models import ClientError
from nodera.widgets import app_icon, label

KEEP = 10
CLEAN = 11
CANCEL = 12
CLEAN_KEEP_PROFILES = 13
PROFILES_ONLY = 14


class UninstallOptionsDialog(AppDialog):
    def __init__(self, clear_other=False, delete_profiles=False, parent=None):
        super().__init__(parent, "Параметры удаления")
        self.setFixedWidth(380)
        layout = self.content_layout()
        self.clear_other = QCheckBox("Очистить настройки, журналы и кэш")
        self.clear_other.setChecked(clear_other)
        layout.addWidget(self.clear_other)
        self.delete_profiles = QCheckBox("Удалить все профили")
        self.delete_profiles.setChecked(delete_profiles)
        layout.addWidget(self.delete_profiles)
        layout.addWidget(
            label(
                "Профили и их копии удаляются только по отдельному выбору. Подписка на сервере сохранится.",
                "muted",
                True,
            )
        )
        footer = QHBoxLayout()
        self.clean_button = QPushButton("Полная очистка")
        self.clean_button.setObjectName("quietAction")
        self.clean_button.setToolTip(
            "Очистить настройки, журналы и кэш. Профили выбираются отдельно."
        )
        self.clean_button.clicked.connect(lambda: self.clear_other.setChecked(True))
        footer.addWidget(self.clean_button)
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


class UninstallDialog(AppDialog):
    def __init__(self):
        super().__init__(title="Nodera")
        self.fit_content = False
        from nodera.installer_ui import INSTALL_STYLE

        self.setStyleSheet(INSTALL_STYLE)
        self.choice = CANCEL
        self.clear_other = False
        self.delete_profiles = False
        self.setFixedSize(510, 374)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(26, 4, 26, 12)
        layout.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(80, 80))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        layout.addWidget(label("Nodera", "installerName"), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(
            label("Удаление приложения", "installerDescription"), 0, Qt.AlignmentFlag.AlignHCenter
        )
        self.status = label("", "installerStatus", True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setMinimumHeight(34)
        layout.addWidget(self.status)
        self.primary = QPushButton("Удалить Nodera")
        self.primary.setObjectName("installerPrimary")
        self.primary.setFixedSize(256, 39)
        self.primary.setAutoDefault(False)
        self.primary.clicked.connect(self.remove)
        layout.addWidget(self.primary, 0, Qt.AlignmentFlag.AlignHCenter)
        links = QHBoxLayout()
        links.addStretch()
        self.options = QPushButton("Настроить")
        self.options.setObjectName("installerLink")
        self.options.clicked.connect(self.show_options)
        cancel = QPushButton("Отмена")
        cancel.setObjectName("installerLink")
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)
        links.addWidget(self.options)
        links.addWidget(cancel)
        links.addStretch()
        layout.addLayout(links)
        layout.addStretch()
        footer = label("Подключение остановится, системный прокси восстановится.", "installerBuild")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(footer)
        self.update_status()

    def update_status(self):
        if self.clear_other:
            text = "Очистка настроек и кэша. " + (
                "Все профили будут удалены." if self.delete_profiles else "Профили сохранятся."
            )
        elif self.delete_profiles:
            text = "Все профили будут удалены. Настройки сохранятся."
        else:
            text = "Профили и настройки сохранятся."
        self.status.setText(text)

    def show_options(self):
        dialog = UninstallOptionsDialog(self.clear_other, self.delete_profiles, self)
        if dialog.exec() == self.DialogCode.Accepted:
            self.clear_other = dialog.clear_other.isChecked()
            self.delete_profiles = dialog.delete_profiles.isChecked()
            self.update_status()

    def choose(self, choice):
        self.choice = choice
        self.accept()

    def remove(self):
        if not self.delete_profiles and not self.clear_other:
            self.choose(KEEP)
        elif confirm(
            self,
            "Удалить Nodera?",
            self.status.text()
            + (
                " Резервные копии профилей также будут удалены без возможности восстановления."
                if self.delete_profiles
                else " Резервные копии профилей сохранятся."
            ),
            "Удалить",
            destructive=True,
        ):
            self.choose(
                CLEAN
                if self.delete_profiles and self.clear_other
                else PROFILES_ONLY
                if self.delete_profiles
                else CLEAN_KEEP_PROFILES
            )


def cleanup_user_data(local_appdata, *, profiles=False, other_data=False):
    """Delete only the two owned directories, never following root reparse points."""
    base = Path(local_appdata).resolve(strict=True)
    targets = [base / name for name in ("NoderaClient", "NoderaClient-install-backups")]
    for target in targets:
        if target.is_symlink() or target.is_junction() or target.resolve().parent != base:
            raise ClientError("Папка данных перенаправлена. Автоматическая очистка отменена.")
    for target in targets:
        if target.exists():
            if profiles and other_data:
                shutil.rmtree(target)
            elif profiles or other_data:
                clean_selected(target, profiles=profiles, other_data=other_data)


def clean_selected(folder, *, profiles, other_data):
    for path in folder.iterdir():
        is_profile = path.suffix.lower() == ".dpapi"
        if path.is_junction() or path.is_symlink():
            if other_data or is_profile and profiles:
                os.rmdir(path) if path.is_junction() else path.unlink()
        elif path.is_dir():
            clean_selected(path, profiles=profiles, other_data=other_data)
        elif (is_profile and profiles) or (not is_profile and other_data):
            path.unlink()
    if not any(folder.iterdir()):
        folder.rmdir()


def handle_uninstall(arguments):
    choice = arguments == ["--uninstall-choice"]
    modes = {"keep", "clean", "cache", "profiles"}
    mode = (
        arguments[0].removeprefix("--finish-uninstall=")
        if len(arguments) == 1 and arguments[0].startswith("--finish-uninstall=")
        else ""
    )
    finish = mode in modes
    if not choice and not finish:
        return None
    if finish:
        from nodera.versioning import build_info

        if not getattr(sys, "frozen", False) or build_info().get("channel") != "release":
            return 1
    app = QApplication.instance() or QApplication(["Nodera"])
    from nodera.installer_ui import configure_style

    configure_style(app)
    if choice:
        dialog = UninstallDialog()
        dialog.exec()
        return dialog.choice
    try:
        from nodera.install_service import stop_client
        from nodera.storage import data_directory

        lock, _ = stop_client(data_directory(), Path(sys.executable).parent, lambda _: None)
        lock.unlock()
        if mode != "keep":
            cleanup_user_data(
                os.environ["LOCALAPPDATA"],
                profiles=mode in {"clean", "profiles"},
                other_data=mode in {"clean", "cache"},
            )
        return 0
    except (ClientError, OSError) as exc:
        QMessageBox.warning(None, "Не удалось завершить удаление", str(exc))
        return 1
