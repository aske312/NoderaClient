"""A small product window; the installation engine has no visible wizard."""

import ctypes
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from nodera.chrome import FramelessWindowMixin
from nodera.install_service import InstallService
from nodera.modals import AppDialog, QMessageBox
from nodera.models import ClientError
from nodera.theme import STYLE
from nodera.theme import configure_style as configure_app_style
from nodera.widgets import app_icon, label

INSTALL_STYLE = """
QLabel#installerName { font-size: 29px; font-weight: 650; color: #edf7f5; }
QLabel#installerDescription { font-size: 13px; color: #a1b9c3; }
QLabel#installerStatus { font-size: 12px; color: #a0bac1; }
QPushButton#installerPrimary { background: #75d3b5; color: #09231d; border: none;
  border-radius: 19px; font-size: 13px; font-weight: 650; padding: 9px 22px; }
QPushButton#installerPrimary:hover { background: #94e3c8; }
QPushButton#installerPrimary:disabled { background: #193d3e; color: #668d8b; }
QPushButton#installerLink { background: transparent; border: none; padding: 3px 7px;
  font-size: 11px; color: #91adb7; font-weight: 400; }
QPushButton#installerLink:hover { color: #d0eee7; text-decoration: underline; }
QLabel#installerBuild { font-size: 9px; color: #506d79; }
QProgressBar#installerProgress { border: none; background: #19343d; border-radius: 2px; }
QProgressBar#installerProgress::chunk { background: #75d3b5; border-radius: 2px; }
QTextBrowser { background: #0d202a; border: 1px solid #28404b; border-radius: 10px; padding: 14px; }
"""


def configure_style(app):
    configure_app_style(app)
    app.setStyleSheet(STYLE + INSTALL_STYLE)


class Worker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, callback, parent):
        super().__init__(parent)
        self.callback = callback

    def run(self):
        try:
            self.result.emit(self.callback(self.progress.emit))
        except ClientError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(
                f"Не удалось продолжить: {exc}. Повторите запуск установщика от имени администратора."
            )


def license_html(text):
    paragraphs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        tag = (
            "h3"
            if re.match(r"^\d+\. ", line) or line.startswith(("NODERA", "MIT License"))
            else "p"
        )
        paragraphs.append(f"<{tag}>{html.escape(line)}</{tag}>")
    return (
        "<html><head><style>body{font-family:Segoe UI;font-size:13px;line-height:1.5;color:#e4eff2}h3{font-size:14px;margin-top:22px}p{margin:10px 0}</style></head><body>"
        + "".join(paragraphs)
        + "</body></html>"
    )


class OptionsDialog(AppDialog):
    def __init__(self, folder, startup, desktop, locked, parent):
        super().__init__(parent)
        self.setWindowTitle("Параметры Nodera")
        self.setMinimumWidth(440)
        layout = self.content_layout()
        form = QFormLayout()
        row = QHBoxLayout()
        self.folder = QLineEdit(str(folder))
        self.folder.setReadOnly(locked)
        browse = QPushButton("Выбрать…")
        browse.setEnabled(not locked)
        browse.clicked.connect(self.browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        form.addRow("Папка", row)
        layout.addLayout(form)
        if locked:
            layout.addWidget(label("Используется папка установленного приложения.", "muted"))
        self.startup = QCheckBox("Запускать с Windows")
        self.startup.setChecked(startup)
        self.desktop = QCheckBox("Ярлык на рабочем столе")
        self.desktop.setChecked(desktop)
        layout.addWidget(self.startup)
        layout.addWidget(self.desktop)
        self.error = label("", "muted", True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Подтвердить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        selected = QFileDialog.getExistingDirectory(self, "Папка приложения", self.folder.text())
        if selected:
            folder = Path(selected)
            if folder.name.casefold() != "nodera client":
                folder /= "Nodera Client"
            self.folder.setText(str(folder))

    def accept(self):
        path = Path(self.folder.text().strip())
        if not path.is_absolute() or path == Path(path.anchor):
            self.error.setText(
                "Укажите полный путь к папке приложения, затем нажмите «Подтвердить»."
            )
            self.error.show()
            return
        super().accept()


class InstallerWindow(FramelessWindowMixin, QMainWindow):
    def __init__(self, service, identity, license_text, folder, *, preview=False):
        super().__init__()
        self.service, self.identity, self.license_text = service, identity, license_text
        self.folder, self.preview = Path(folder), preview
        self.plan = None
        self.startup, self.desktop = True, False
        self.worker = None
        self.exit_when_done = False
        self.finished = False
        self.reconnect = False
        self.setWindowTitle("Nodera")
        self.setWindowIcon(app_icon())
        self.setFixedSize(510, 374)
        body = QWidget()
        self.configure_shell(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(26, 4, 26, 12)
        layout.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(80, 80))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        layout.addWidget(label("Nodera", "installerName"), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(
            label("Клиент для безопасных подключений", "installerDescription"),
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        self.status = label("Проверяем приложение…", "installerStatus", True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setMinimumHeight(34)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setObjectName("installerProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.primary = QPushButton("Принять и установить")
        self.primary.setObjectName("installerPrimary")
        self.primary.setFixedSize(256, 39)
        self.primary.setEnabled(False)
        self.primary.clicked.connect(self.activate)
        layout.addWidget(self.primary, 0, Qt.AlignmentFlag.AlignHCenter)
        links = QHBoxLayout()
        links.setSpacing(6)
        links.addStretch()
        self.terms = self.link("Условия", self.show_terms)
        self.options = self.link("Настроить", self.show_options)
        self.repair = self.link("Восстановить", self.force_repair)
        self.repair.hide()
        for button in (self.terms, self.options, self.repair):
            links.addWidget(button)
        links.addStretch()
        layout.addLayout(links)
        layout.addStretch()
        footer = QHBoxLayout()
        build = label(
            f"v{identity['version']} build:{identity.get('revision') or identity.get('build', '')}",
            "installerBuild",
        )
        shadow = QGraphicsDropShadowEffect(build)
        shadow.setOffset(0, 1)
        shadow.setBlurRadius(0)
        shadow.setColor(QColor("#25414b"))
        build.setGraphicsEffect(shadow)
        footer.addWidget(build)
        footer.addStretch()
        if preview:
            footer.addWidget(label("Предпросмотр", "installerBuild"))
        layout.addLayout(footer)
        QTimer.singleShot(0, self.inspect)

    def link(self, text, callback):
        button = QPushButton(text)
        button.setObjectName("installerLink")
        button.clicked.connect(callback)
        return button

    def run_task(self, callback, success):
        self.primary.setEnabled(False)
        self.options.setEnabled(False)
        self.repair.setEnabled(False)
        task = Worker(callback, self)
        self.worker = task
        task.progress.connect(self.status.setText)
        task.result.connect(lambda value: None if self.exit_when_done else success(value))
        task.failed.connect(self.failed)

        def finished():
            self.worker = None
            task.deleteLater()
            if self.exit_when_done:
                self.close()
            else:
                self.primary.setEnabled(True)
                self.options.setEnabled(not self.finished)
                self.repair.setEnabled(True)

        task.finished.connect(finished)
        task.start()

    def inspect(self):
        self.run_task(lambda _: self.service.inspect(self.folder), self.inspected)

    def inspected(self, result):
        self.plan, self.startup, self.desktop = result
        self.folder = self.plan.folder
        if self.plan.action == "current":
            self.status.setText("Актуальная версия уже установлена")
            self.primary.setText("Открыть Nodera")
            self.repair.show()
        else:
            self.status.setText("Всё готово")
            self.primary.setText("Принять и установить")

    def show_terms(self):
        dialog = AppDialog(self)
        dialog.fit_content = False
        dialog.setWindowTitle("Условия использования Nodera")
        dialog.resize(640, 440)
        layout = dialog.content_layout()
        reader = QTextBrowser()
        reader.setHtml(license_html(self.license_text))
        layout.addWidget(reader)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def show_options(self):
        dialog = OptionsDialog(
            self.folder, self.startup, self.desktop, bool(self.plan and self.plan.installed), self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.folder = Path(dialog.folder.text().strip())
            self.startup, self.desktop = dialog.startup.isChecked(), dialog.desktop.isChecked()

    def activate(self):
        if self.plan is None:
            self.inspect()
            return
        if self.finished or (self.plan and self.plan.action == "current"):
            self.open_client()
        else:
            self.begin(False)

    def force_repair(self):
        # An explicit repair still needs an explicit license acknowledgement.
        self.finished = False
        if (
            QMessageBox.question(
                self,
                "Nodera",
                "Восстановить файлы приложения на условиях лицензии Nodera? Ваши данные сохранятся.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.begin(True)

    def begin(self, force_repair):
        if not self.plan or self.worker:
            return
        if self.preview:
            self.finished = True
            self.status.setText("Предпросмотр завершён. Файлы и настройки не изменены.")
            self.primary.setText("Готово")
            self.options.setEnabled(False)
            return
        from nodera.install_service import Plan

        plan = Plan(self.plan.action, self.folder, self.plan.installed)
        self.progress.show()
        self.primary.setText("Подготавливаем…")
        self.repair.hide()
        self.run_task(
            lambda progress: self.service.run(
                plan, self.startup, self.desktop, progress, force_repair=force_repair
            ),
            self.completed,
        )

    def completed(self, result):
        self.finished = True
        self.folder = Path(result["folder"])
        self.reconnect = self.reconnect or result["reconnect"]
        self.progress.hide()
        self.primary.setText("Открыть Nodera")
        self.status.setText(result["warning"] or "Всё готово. Можно начинать.")

    def failed(self, message):
        self.progress.hide()
        self.primary.setText("Повторить")
        self.status.setText("Не удалось завершить подготовку")
        if not self.exit_when_done:
            QMessageBox.warning(self, "Nodera", message)

    def open_client(self):
        if self.preview:
            self.close()
            return
        try:
            arguments = [str(self.folder / "Nodera.exe")]
            if self.reconnect:
                arguments.append("--connect")
            subprocess.Popen(arguments, cwd=self.folder)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Nodera",
                f"Не удалось открыть приложение: {exc}. Запустите Nodera из меню «Пуск».",
            )
            return
        self.close()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.exit_when_done = True
            self.hide()
            event.ignore()
            return
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Nodera")
    configure_style(app)
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        QMessageBox.warning(
            None,
            "Nodera",
            "Запустите Nodera-Setup.exe от имени администратора и разрешите изменения в запросе Windows.",
        )
        return 2
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    assets = bundle / "installer"
    identity = json.loads((assets / "build-info.json").read_text(encoding="utf-8"))
    manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
    preview = (assets / "preview.json").exists() or "--preview" in sys.argv
    root = Path(os.environ["LOCALAPPDATA"]) / "NoderaClient"
    service = InstallService(
        identity, manifest, bundle / "engine/Nodera-Engine.exe", root, preview=preview
    )
    terms = (
        (assets / "license-ru.txt").read_text(encoding="utf-8")
        + "\n\n"
        + (assets / "LICENSE").read_text(encoding="utf-8")
    )
    folder = Path(os.environ.get("ProgramW6432", os.environ["ProgramFiles"])) / "Nodera Client"
    for argument in sys.argv[1:]:
        if argument.startswith("/DIR=") and Path(argument[5:]).is_absolute():
            folder = Path(argument[5:])
    window = InstallerWindow(service, identity, terms, folder, preview=preview)
    window.reconnect = "/NODERARESUME=1" in sys.argv
    window.show()
    return app.exec()
