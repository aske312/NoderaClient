import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.controls import ComboBox
from nodera.theme import STYLE


def test_popup_keyboard_selection_and_cancel():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    combo = ComboBox()
    combo.addItem("TUN (VPN)", "tun")
    combo.addItem("Proxy (HTTP / SOCKS5)", "proxy")
    combo.resize(260, 38)
    combo.show()
    combo.showPopup()
    app.processEvents()
    assert combo.view().window().height() <= combo.view().height() + 2
    assert not combo.view().window().mask().contains(combo.view().window().rect().topLeft())
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentData() == "proxy"
    combo.showPopup()
    app.processEvents()
    QTest.keyClick(combo.view(), Qt.Key.Key_Up)
    QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
    assert combo.currentData() == "proxy"
    combo.close()


def test_long_popup_scroll_and_mouse_selection():
    app = QApplication.instance() or QApplication([])
    combo = ComboBox()
    for index in range(40):
        combo.addItem(f"Country {index} — a longer server location", index)
    combo.resize(180, 38)
    combo.show()
    combo.showPopup()
    app.processEvents()
    view = combo.view()
    assert view.width() > combo.width()
    assert view.verticalScrollBar().maximum() > 0
    index = combo.model().index(30, 0)
    view.scrollTo(index)
    app.processEvents()
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center()
    )
    assert combo.currentData() == 30
    combo.close()
