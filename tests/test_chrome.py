import ctypes
import os
import time
from ctypes import wintypes

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.chrome import NativeMinMaxInfo, native_work_area, resized_rect, work_area_limits
from nodera.models import Settings
from nodera.storage import Store
from nodera.ui import MainWindow


@pytest.mark.skipif(os.name != "nt", reason="Windows monitor API")
def test_windows_message_layout_and_monitor_api():
    user = ctypes.WinDLL("user32")
    user.GetDesktopWindow.restype = wintypes.HWND
    message = wintypes.MSG()
    limits = NativeMinMaxInfo()
    message.hWnd = user.GetDesktopWindow()
    message.message = 0x0024
    message.lParam = ctypes.addressof(limits)
    if not native_work_area(ctypes.addressof(message), QSize(840, 620), 1.0):
        pytest.skip("No monitor exposed by Windows session")
    assert limits.max_size.x > 0 and limits.max_size.y > 0
    assert (limits.min_track.x, limits.min_track.y) == (840, 620)


def test_maximized_window_uses_work_area_and_scales_minimum_size():
    limits = NativeMinMaxInfo()
    monitor = wintypes.RECT(-1920, 0, 0, 1080)
    work = wintypes.RECT(-1880, 0, 0, 1040)
    work_area_limits(limits, monitor, work, QSize(840, 620), 1.5)
    assert (limits.max_position.x, limits.max_position.y) == (40, 0)
    assert (limits.max_size.x, limits.max_size.y) == (1880, 1040)
    assert (limits.min_track.x, limits.min_track.y) == (1260, 930)


def test_resize_corners_preserve_opposite_edge_and_minimum_size():
    original = QRect(100, 100, 940, 660)
    edges = Qt.Edge.LeftEdge | Qt.Edge.TopEdge
    resized = resized_rect(original, QPoint(2000, 2000), edges, QSize(840, 620))
    assert resized.size() == QSize(840, 620)
    assert resized.bottomRight() == original.bottomRight()
    grown = resized_rect(
        original, QPoint(40, 50), Qt.Edge.RightEdge | Qt.Edge.BottomEdge, QSize(840, 620)
    )
    assert grown.topLeft() == original.topLeft()
    assert grown.size() == QSize(980, 710)


def test_macos_buttons_minimize_and_close_without_maximize(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(), None)
    window.timer.stop()
    window.show()
    app.processEvents()
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    assert not hasattr(window.title_bar, "maximize")
    assert window.title_bar.close_button.x() > window.title_bar.minimize.x() > window.width() - 80
    assert all(handle.isVisible() for handle in window.resize_handles)
    QTest.mouseDClick(window.title_bar, Qt.MouseButton.LeftButton, pos=QPoint(250, 20))
    app.processEvents()
    assert not window.isMaximized()
    window.showMaximized()
    app.processEvents()
    assert not window.isMaximized()
    window.showFullScreen()
    app.processEvents()
    assert not window.isFullScreen()
    window.title_bar.minimize.click()
    app.processEvents()
    assert window.isMinimized()
    window.showNormal()
    app.processEvents()
    window.title_bar.close_button.click()
    app.processEvents()
    assert not window.isVisible()


def test_custom_close_waits_for_core_shutdown(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(), None)
    window.timer.stop()
    window.show()
    window.core.process = object()
    stopped = []

    def stop(*args):
        window.core.process = None
        stopped.append(True)

    monkeypatch.setattr(window.core, "stop", stop)
    window.title_bar.close_button.click()
    deadline = time.monotonic() + 5
    while window.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
        time.sleep(0.01)
    assert stopped == [True]
    assert not window.isVisible()
