"""Application-owned window chrome with system move/resize and Qt fallback."""

import ctypes
import os
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QRegion,
    QShortcut,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QPushButton, QVBoxLayout, QWidget

from nodera.widgets import app_icon, label


class NativeMinMaxInfo(ctypes.Structure):
    _fields_ = [
        (name, wintypes.POINT)
        for name in ("reserved", "max_size", "max_position", "min_track", "max_track")
    ]


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("monitor", wintypes.RECT),
        ("work", wintypes.RECT),
        ("flags", wintypes.DWORD),
    ]


def work_area_limits(limits, monitor, work, minimum, scale):
    limits.max_position.x = work.left - monitor.left
    limits.max_position.y = work.top - monitor.top
    limits.max_size.x = work.right - work.left
    limits.max_size.y = work.bottom - work.top
    limits.min_track.x = round(minimum.width() * scale)
    limits.min_track.y = round(minimum.height() * scale)


def native_work_area(message, minimum, scale):
    msg = wintypes.MSG.from_address(int(message))
    if msg.message != 0x0024 or not msg.lParam:  # WM_GETMINMAXINFO
        return False
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.MonitorFromWindow.restype = wintypes.HANDLE
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user.GetMonitorInfoW.restype = wintypes.BOOL
    monitor = user.MonitorFromWindow(msg.hWnd, 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if not monitor or not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    limits = NativeMinMaxInfo.from_address(msg.lParam)
    work_area_limits(limits, info.monitor, info.work, minimum, scale)
    return True


def resized_rect(rect: QRect, delta: QPoint, edges, minimum) -> QRect:
    result = QRect(rect)
    if edges & Qt.Edge.LeftEdge:
        result.setLeft(min(rect.left() + delta.x(), rect.right() - minimum.width() + 1))
    if edges & Qt.Edge.RightEdge:
        result.setRight(max(rect.right() + delta.x(), rect.left() + minimum.width() - 1))
    if edges & Qt.Edge.TopEdge:
        result.setTop(min(rect.top() + delta.y(), rect.bottom() - minimum.height() + 1))
    if edges & Qt.Edge.BottomEdge:
        result.setBottom(max(rect.bottom() + delta.y(), rect.top() + minimum.height() - 1))
    return result


class WindowButton(QPushButton):
    def __init__(self, role, window):
        super().__init__()
        self.role, self.target = role, window
        self.setFixedSize(26, 24)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        name = {"minimize": "Свернуть", "close": "Закрыть"}[role]
        self.setToolTip(name)
        self.setAccessibleName(name)
        self.clicked.connect(self.activate)

    def activate(self):
        if self.role == "minimize":
            self.target.showMinimized()
        else:
            self.target.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse() or self.hasFocus()
        painter.setPen(Qt.PenStyle.NoPen)
        if hover or self.isDown():
            painter.setBrush(QColor("#62383d" if self.role == "close" else "#1c3945"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        painter.setPen(
            QPen(QColor("#eeb5b5" if hover and self.role == "close" else "#91adb8"), 1.3)
        )
        if self.role == "minimize":
            painter.drawLine(9, 13, 17, 13)
        else:
            painter.drawLine(9, 8, 17, 16)
            painter.drawLine(17, 8, 9, 16)


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__()
        self.target = window
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(34)
        self._drag = None
        self._native_drag = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 8, 4)
        layout.setSpacing(4)
        icon = label("")
        icon.setPixmap(app_icon().pixmap(18, 18))
        title = label("Nodera", "windowTitle")
        window.windowTitleChanged.connect(title.setText)
        for widget in (icon, title):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(widget)
        layout.addStretch()
        self.minimize = WindowButton("minimize", window)
        self.close_button = WindowButton("close", window)
        for button in (self.minimize, self.close_button):
            layout.addWidget(button)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().toPoint(), self.target.pos())
            handle = self.target.windowHandle()
            self._native_drag = bool(handle and handle.startSystemMove())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag and event.buttons() & Qt.MouseButton.LeftButton and not self._native_drag:
            start, position = self._drag
            if self.target.isMaximized():
                self.target.showNormal()
                position = event.globalPosition().toPoint() - QPoint(self.target.width() // 2, 20)
                start = event.globalPosition().toPoint()
                self._drag = (start, position)
            self.target.move(position + event.globalPosition().toPoint() - start)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        self._drag = None
        event.accept()

    def contextMenuEvent(self, event):
        self.show_menu(event.globalPos())

    def show_menu(self, position):
        menu = QMenu(self)
        menu.addAction("Свернуть", self.target.showMinimized)
        menu.addSeparator()
        menu.addAction("Закрыть", self.target.close)
        menu.exec(position)


class ResizeHandle(QWidget):
    def __init__(self, window, edges, cursor):
        super().__init__(window)
        self.target, self.edges = window, edges
        self.setCursor(cursor)
        self.setStyleSheet("background: transparent;")
        self._drag = None
        self._native_drag = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().toPoint(), self.target.geometry())
            handle = self.target.windowHandle()
            self._native_drag = bool(handle and handle.startSystemResize(self.edges))
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag and event.buttons() & Qt.MouseButton.LeftButton and not self._native_drag:
            position, geometry = self._drag
            self.target.setGeometry(
                resized_rect(
                    geometry,
                    event.globalPosition().toPoint() - position,
                    self.edges,
                    self.target.minimumSize(),
                )
            )

    def mouseReleaseEvent(self, event):
        self._drag = None


class ShellSurface(QFrame):
    """One continuous painted surface behind the caption, pages and navigation."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        field = QRadialGradient(
            QPointF(self.width() * 0.30, self.height() * 0.30), self.width() * 0.85
        )
        field.setColorAt(0, QColor("#0d202b"))
        field.setColorAt(0.6, QColor("#091720"))
        field.setColorAt(1, QColor("#07121a"))
        painter.setBrush(field)
        painter.setPen(QPen(QColor("#29404b"), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class FramelessWindowMixin:
    def nativeEvent(self, event_type, message):
        if os.name == "nt" and native_work_area(
            message, self.minimumSize(), self.devicePixelRatioF()
        ):
            return True, 0
        return super().nativeEvent(event_type, message)

    def configure_shell(self, body):
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        frame = ShellSurface()
        frame.setObjectName("windowFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.title_bar = TitleBar(self)
        self.window_menu_shortcut = QShortcut(QKeySequence("Alt+Space"), self)
        self.window_menu_shortcut.activated.connect(
            lambda: self.title_bar.show_menu(
                self.title_bar.mapToGlobal(QPoint(12, self.title_bar.height()))
            )
        )
        layout.addWidget(self.title_bar)
        body.setObjectName("windowBody")
        layout.addWidget(body, 1)
        self.setCentralWidget(frame)
        left, right, top, bottom = (
            Qt.Edge.LeftEdge,
            Qt.Edge.RightEdge,
            Qt.Edge.TopEdge,
            Qt.Edge.BottomEdge,
        )
        definitions = [
            (left, Qt.CursorShape.SizeHorCursor),
            (right, Qt.CursorShape.SizeHorCursor),
            (top, Qt.CursorShape.SizeVerCursor),
            (bottom, Qt.CursorShape.SizeVerCursor),
            (left | top, Qt.CursorShape.SizeFDiagCursor),
            (right | top, Qt.CursorShape.SizeBDiagCursor),
            (left | bottom, Qt.CursorShape.SizeBDiagCursor),
            (right | bottom, Qt.CursorShape.SizeFDiagCursor),
        ]
        self.resize_handles = [ResizeHandle(self, edges, cursor) for edges, cursor in definitions]
        self.update_shell()

    def update_shell(self):
        if not hasattr(self, "resize_handles"):
            return
        maximized = self.isMaximized()
        resizable = self.minimumSize() != self.maximumSize()
        if QGuiApplication.platformName() != "offscreen":
            if maximized:
                self.clearMask()
            else:
                path = QPainterPath()
                path.addRoundedRect(self.rect(), 12, 12)
                self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        w, h, border, corner = self.width(), self.height(), 5, 10
        rectangles = [
            (0, corner, border, h - 2 * corner),
            (w - border, corner, border, h - 2 * corner),
            (corner, 0, w - 2 * corner, border),
            (corner, h - border, w - 2 * corner, border),
            (0, 0, corner, corner),
            (w - corner, 0, corner, corner),
            (0, h - corner, corner, corner),
            (w - corner, h - corner, corner, corner),
        ]
        for handle, rectangle in zip(self.resize_handles, rectangles, strict=True):
            handle.setGeometry(*rectangle)
            handle.setVisible(not maximized and resizable)
            handle.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_shell()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMaximized() or self.isFullScreen():
                self.showNormal()
            self.update_shell()
