import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from nodera.route_editor import RouteDialog


def test_route_rows_preserve_edits_when_reordered_removed_and_added():
    app = QApplication.instance() or QApplication([])
    dialog = RouteDialog(
        [
            {"role": "relay", "country": "DE", "host": "relay.example"},
            {"role": "server", "country": "FI", "host": "origin.example"},
        ]
    )
    first, second = dialog.hop_rows
    second.host.setText("edited.example")
    second.up.click()
    assert dialog.route()[0] == {"role": "server", "country": "FI", "host": "edited.example"}
    first.remove.click()
    assert len(dialog.route()) == 1
    assert not second.remove.isEnabled()
    for _ in range(10):
        dialog.add_hop({"role": "cdn", "country": ""})
    assert len(dialog.route()) == 8 and not dialog.add_button.isEnabled()
    dialog.hop_rows[-1].remove.click()
    assert dialog.add_button.isEnabled()
    dialog.reset_route()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.route() is None
    dialog.close()
    app.processEvents()


def test_route_editor_rejects_url_in_address_and_keeps_draft_for_correction():
    app = QApplication.instance() or QApplication([])
    dialog = RouteDialog([])
    dialog.hop_rows[0].host.setText("https://example.com/private")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "Участок 1" in dialog.message.text()
    dialog.hop_rows[0].host.setText("2001:db8::1")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.route()[0]["host"] == "2001:db8::1"
    dialog.close()
    app.processEvents()
