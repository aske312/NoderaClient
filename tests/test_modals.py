import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from nodera.dialogs import SubscriptionDialog
from nodera.modals import MessageDialog, get_text
from nodera.uninstall import (
    CANCEL,
    CLEAN,
    CLEAN_KEEP_PROFILES,
    PROFILES_ONLY,
    UninstallDialog,
    UninstallOptionsDialog,
)


def test_rename_reads_edited_text_after_confirmation():
    app = QApplication.instance() or QApplication([])

    def enter():
        dialog = app.activeModalWidget()
        from PySide6.QtWidgets import QLineEdit

        dialog.findChild(QLineEdit).setText("Новое имя")
        dialog.accept()

    QTimer.singleShot(0, enter)
    assert get_text(None, "Название", "Подписка", text="Старое имя") == ("Новое имя", True)


def test_destructive_confirmation_defaults_to_cancel_and_escape_preserves_data():
    app = QApplication.instance() or QApplication([])
    dialog = MessageDialog(
        None, "Удалить?", "Профили будут удалены.", "Удалить всё", destructive=True, cancel=True
    )
    dialog.show()
    app.processEvents()
    assert dialog.cancel_button.isDefault()
    assert not dialog.action.autoDefault()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.isVisible()


def test_uninstall_full_cleanup_requires_second_confirmation(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = UninstallDialog()
    dialog.clear_other = True
    dialog.update_status()
    monkeypatch.setattr("nodera.uninstall.confirm", lambda *a, **k: False)
    dialog.primary.click()
    assert dialog.choice == CANCEL
    monkeypatch.setattr("nodera.uninstall.confirm", lambda *a, **k: True)
    dialog.primary.click()
    assert dialog.choice == CLEAN_KEEP_PROFILES
    dialog.delete_profiles = True
    dialog.update_status()
    dialog.primary.click()
    assert dialog.choice == CLEAN
    dialog.clear_other = False
    dialog.update_status()
    dialog.primary.click()
    assert dialog.choice == PROFILES_ONLY
    dialog.close()
    app.processEvents()


def test_uninstall_options_require_save_and_do_not_select_profiles_implicitly():
    app = QApplication.instance() or QApplication([])
    window = UninstallDialog()

    def edit(accept):
        dialog = app.activeModalWidget()
        assert isinstance(dialog, UninstallOptionsDialog)
        assert not dialog.delete_profiles.isChecked()
        dialog.clean_button.click()
        assert dialog.clear_other.isChecked() and not dialog.delete_profiles.isChecked()
        dialog.delete_profiles.setChecked(True)
        dialog.accept() if accept else dialog.reject()

    QTimer.singleShot(0, lambda: edit(False))
    window.options.click()
    assert not window.clear_other and not window.delete_profiles
    assert window.choice == CANCEL
    QTimer.singleShot(0, lambda: edit(True))
    window.options.click()
    assert window.clear_other and window.delete_profiles
    assert "Все профили будут удалены" in window.status.text()
    assert window.choice == CANCEL
    window.close()
    app.processEvents()


def test_subscription_validation_keeps_modal_open_until_valid():
    app = QApplication.instance() or QApplication([])
    dialog = SubscriptionDialog()
    dialog.url.setText("invalid")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.message.text()
    dialog.url.setText("https://example.invalid/subscription")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()
    app.processEvents()
