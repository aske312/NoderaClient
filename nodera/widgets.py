"""Application icon, code-drawn artwork, and controls."""

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListView,
    QListWidget,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStylePainter,
    QToolTip,
    QVBoxLayout,
)


def label(text: str, kind: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(kind)
    widget.setWordWrap(wrap)
    return widget


class ElidedLabel(QLabel):
    """Keep the full value available while painting one bounded line."""

    def __init__(self):
        super().__init__()
        self.setTextFormat(Qt.TextFormat.PlainText)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def paintEvent(self, event):
        painter = QStylePainter(self)
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width()
        )
        painter.drawItemText(
            self.contentsRect(),
            self.alignment(),
            self.palette(),
            self.isEnabled(),
            text,
            self.foregroundRole(),
        )


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 11, 12, 11)
    layout.setSpacing(6)
    return frame, layout


def app_icon(size: int = 64) -> QIcon:
    from pathlib import Path

    icon = QIcon(str(Path(__file__).parent / "resources/nodera.ico"))
    if not icon.isNull():
        return icon
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 64, size / 64)
    painter.setBrush(QColor("#11212c"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, 64, 64, 12, 12)
    painter.setPen(
        QPen(
            QColor("#61cfd8"),
            5,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.drawPolyline(
        QPolygonF([QPointF(22, 44), QPointF(22, 20), QPointF(42, 44), QPointF(42, 20)])
    )
    painter.end()
    return QIcon(pixmap)


def line_icon(kind: str) -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#91a6b3"), 1.6))
    if kind == "home":
        painter.drawPolyline(QPolygonF([QPointF(3, 11), QPointF(12, 4), QPointF(21, 11)]))
        painter.drawRoundedRect(6, 10, 12, 10, 2, 2)
    elif kind == "servers":
        for y in (4, 12):
            painter.drawRoundedRect(3, y, 18, 6, 2, 2)
            painter.drawPoint(7, y + 3)
            painter.drawLine(13, y + 3, 17, y + 3)
    elif kind == "routes":
        painter.drawLine(5, 5, 5, 18)
        painter.drawLine(5, 9, 18, 9)
        painter.drawLine(5, 18, 18, 18)
        for x, y in ((5, 4), (19, 9), (19, 18)):
            painter.drawEllipse(QPointF(x, y), 2, 2)
    elif kind == "profile":
        painter.drawRoundedRect(4, 3, 16, 18, 3, 3)
        painter.drawEllipse(QPointF(12, 9), 2.5, 2.5)
        painter.drawArc(QRectF(7, 12, 10, 7), 0, 180 * 16)
    elif kind == "settings":
        painter.drawEllipse(QPointF(12, 12), 6, 6)
        painter.drawEllipse(QPointF(12, 12), 2, 2)
        for i in range(8):
            a = i * math.pi / 4
            painter.drawLine(
                QPointF(12 + 6 * math.cos(a), 12 + 6 * math.sin(a)),
                QPointF(12 + 9 * math.cos(a), 12 + 9 * math.sin(a)),
            )
    elif kind == "refresh":
        painter.drawArc(QRectF(5, 5, 14, 14), 35 * 16, 290 * 16)
        painter.drawPolyline(QPolygonF([QPointF(15, 3), QPointF(20, 5), QPointF(18, 10)]))
    elif kind == "sites":
        painter.drawEllipse(QPointF(12, 12), 8, 8)
        painter.drawEllipse(QRectF(8, 4, 8, 16))
        painter.drawLine(4, 12, 20, 12)
    elif kind == "connections":
        painter.drawLine(4, 7, 20, 7)
        painter.drawLine(4, 17, 20, 17)
        painter.drawPolyline(QPolygonF([QPointF(16, 3), QPointF(20, 7), QPointF(16, 11)]))
        painter.drawPolyline(QPolygonF([QPointF(8, 13), QPointF(4, 17), QPointF(8, 21)]))
    elif kind == "info":
        painter.drawEllipse(QPointF(12, 12), 8, 8)
        painter.drawPoint(QPointF(12, 8))
        painter.drawLine(12, 11, 12, 17)
    elif kind == "diagnostics":
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(2, 13),
                    QPointF(7, 13),
                    QPointF(10, 5),
                    QPointF(14, 20),
                    QPointF(17, 11),
                    QPointF(22, 11),
                ]
            )
        )
    elif kind == "chevron-up":
        painter.drawPolyline(QPolygonF([QPointF(6, 15), QPointF(12, 9), QPointF(18, 15)]))
    elif kind in {"collapse", "expand"}:
        painter.drawRoundedRect(3, 4, 18, 16, 3, 3)
        painter.drawLine(16, 5, 16, 19)
        x = 8 if kind == "collapse" else 12
        middle = 12 if kind == "collapse" else 8
        painter.drawPolyline(QPolygonF([QPointF(x, 8), QPointF(middle, 12), QPointF(x, 16)]))
    else:
        for y in (6, 12, 18):
            painter.drawPoint(4, y)
            painter.drawLine(8, y, 21, y)
    painter.end()
    return QIcon(pixmap)


class AddProfileButton(QPushButton):
    def __init__(self):
        super().__init__()
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#20444f" if self.underMouse() else "#102e39"))
        painter.setPen(QPen(QColor("#70c9c2" if self.hasFocus() else "#365865"), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        painter.setPen(
            QPen(
                QColor("#b0e5df" if self.isEnabled() else "#536d76"),
                1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        center = QRectF(self.rect()).center()
        painter.drawLine(center + QPointF(-5, 0), center + QPointF(5, 0))
        painter.drawLine(center + QPointF(0, -5), center + QPointF(0, 5))


class ConnectButton(QPushButton):
    def __init__(self):
        super().__init__()
        self.setFixedSize(164, 164)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._busy = False
        self._subscription = None
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._advance)
        self.toggled.connect(self._sync_timer)

    def setBusy(self, busy):
        self._busy = busy
        self._sync_timer()

    def setSubscriptionStatus(self, status):
        self._subscription = status
        self._sync_timer()

    def _sync_timer(self, *_):
        if self.isVisible() and (self.isChecked() or self._busy or self._subscription == "loading"):
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _advance(self):
        self._phase = (
            self._phase + (8 if self._busy or self._subscription == "loading" else 2.4)
        ) % 360
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 164, self.height() / 164)
        active = self.isChecked()
        enabled = self.isEnabled() or active or self._busy
        accent = QColor(("#69c8aa" if active else "#61cfd8") if enabled else "#466170")
        glow = QRadialGradient(QPointF(82, 82), 81)
        glow.setColorAt(0, QColor(97, 207, 216, 36 if enabled else 8))
        glow.setColorAt(0.72, QColor(97, 207, 216, 18 if enabled else 5))
        glow.setColorAt(1, QColor(97, 207, 216, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(1, 1, 162, 162))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(97, 207, 216, 27), 1))
        painter.drawEllipse(QRectF(11, 11, 142, 142))
        if active or self._busy:
            painter.setPen(QPen(accent, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(11, 11, 142, 142), int(self._phase * 16), 85 * 16)
        fill = QLinearGradient(0, 24, 0, 140)
        fill.setColorAt(0, QColor("#153e36" if active else "#15313e"))
        fill.setColorAt(1, QColor("#0c2624" if active else "#0a1d28"))
        painter.setBrush(fill)
        painter.setPen(
            QPen(
                accent
                if self.underMouse() or self.hasFocus()
                else QColor("#386758" if active else "#2c515f"),
                1.3,
            )
        )
        painter.drawEllipse(QRectF(23, 23, 118, 118))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(61, 65, 42, 42), 135 * 16, 270 * 16)
        painter.drawLine(QPointF(82, 57), QPointF(82, 80))
        if self._subscription:
            color = {"loading": "#87cde1", "success": "#86d7aa", "error": "#e2ad75"}[
                self._subscription
            ]
            painter.setPen(QPen(QColor(color), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            if self._subscription == "loading":
                painter.drawArc(QRectF(3, 3, 158, 158), int(-self._phase * 16), 110 * 16)
            else:
                painter.drawEllipse(QRectF(3, 3, 158, 158))


class RouteButton(QPushButton):
    """A long server name must not move the central power control."""

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(min(270, hint.width()), hint.height())

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        width = max(0, self.width() - self.iconSize().width() - 16)
        option.text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, width)
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)


class NodeDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(max(200, self.parent().viewport().width() - 2), 54)

    def paint(self, painter, option, index):
        from nodera.server_assets import flag_pixmap, protocol_status_pixmap, route_visual_state

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(1, 1, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        data = index.data(Qt.ItemDataRole.UserRole + 1) or {}
        health = route_visual_state(data)
        health_color = {"frozen": "#6f9faf", "error": "#b26d73", "slow": "#b49764"}.get(health)
        frozen = bool(data.get("frozen"))
        active = bool(data.get("active")) and not frozen
        current = bool(data.get("current")) and not frozen
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.fillRect(
            rect,
            QColor(
                "#152a35"
                if frozen
                else "#2a2127"
                if health == "error"
                else "#29271f"
                if health == "slow"
                else "#14343e"
                if current
                else "#152e29"
                if active
                else "#152b27"
                if data.get("forced")
                else "#142f39"
                if selected or hovered
                else "#0c1e28"
                if index.row() % 2 == 0
                else "#0e222b"
            ),
        )
        painter.setPen(QPen(QColor("#1b3440"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if health_color or active or current:
            painter.fillRect(
                QRectF(rect.left(), rect.top() + 4, 3, rect.height() - 8),
                QColor(health_color or ("#5998a6" if current else "#679681")),
            )
        if selected or current:
            painter.setPen(QPen(QColor("#5998a6" if current else "#4e8394"), 1))
            painter.drawRect(rect.adjusted(4, 1, -1, -1))
        center = QPointF(rect.left() + 18, rect.center().y())
        star = QPolygonF()
        for step in range(10):
            angle = -math.pi / 2 + step * math.pi / 5
            radius = 8 if step % 2 == 0 else 3.6
            star.append(center + QPointF(math.cos(angle) * radius, math.sin(angle) * radius))
        enabled = self.parent().pin_enabled and (
            data.get("forced") or data.get("can_force") and not data.get("frozen")
        )
        painter.setPen(
            QPen(
                QColor("#b19d6b" if data.get("forced") else "#78959f" if enabled else "#354c57"),
                1.3,
            )
        )
        painter.setBrush(QColor("#b19d6b") if data.get("forced") else Qt.BrushStyle.NoBrush)
        painter.drawPolygon(star)
        icon = protocol_status_pixmap(str(data.get("type", "")), health)
        painter.drawPixmap(
            QRectF(rect.left() + 32, rect.top() + 5, 40, 40), icon, QRectF(icon.rect())
        )
        left = rect.left() + 76
        font = option.font
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e2edf4"))
        title = str(index.data(Qt.ItemDataRole.UserRole) or "")
        delay = data.get("delay")
        delay_text = (
            "Не проверен" if delay is None else f"{delay} мс" if delay > 0 else "Нет ответа"
        )
        delay_width = painter.fontMetrics().horizontalAdvance(delay_text)
        status = "Заморожен" if frozen else "Текущий" if current else "В работе" if active else ""
        status_width = painter.fontMetrics().horizontalAdvance(status) + 16 if status else 0
        right_reserved = max(100, delay_width + status_width + (34 if status else 24))
        title = painter.fontMetrics().elidedText(
            title, Qt.TextElideMode.ElideRight, max(0, int(rect.width() - 76 - right_reserved))
        )
        painter.drawText(QPointF(left, rect.top() + 21), title)
        if status:
            badge = QRectF(
                rect.right() - delay_width - status_width - 22, rect.top() + 5, status_width, 22
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#203743" if frozen else "#1b434e" if current else "#1c3d33"))
            painter.drawRoundedRect(badge, 6, 6)
            painter.setPen(QColor("#91b0bf" if frozen else "#96bac2" if current else "#94b8a6"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, status)
        text = delay_text
        painter.setPen(QColor(health_color or ("#7fa99f" if delay and delay > 0 else "#6f8a9c")))
        painter.drawText(
            QPointF(
                rect.right() - painter.fontMetrics().horizontalAdvance(text) - 12, rect.top() + 21
            ),
            text,
        )
        font.setPixelSize(10)
        font.setBold(False)
        painter.setFont(font)
        codes = data.get("countries", [])
        flag_width = min(len(codes), 8) * 24
        x = rect.right() - flag_width - 10
        visible_codes = codes[:7] if len(codes) > 8 else codes
        for code in visible_codes:
            flag = flag_pixmap(code)
            painter.drawPixmap(QRectF(x, rect.top() + 32, 20, 14), flag, QRectF(flag.rect()))
            x += 24
        if len(codes) > 8:
            painter.setPen(QColor("#9dbbc5"))
            painter.drawText(QPointF(x, rect.top() + 43), f"+{len(codes) - 7}")
        painter.setPen(QColor("#7e9eac"))
        text = str(data.get("type", "")).upper()
        if data.get("frozen"):
            text = (
                "Повторная проверка через " + str(data["retry_in"]) + " с · "
                if data.get("retry_in")
                else "Ожидает успешной проверки · "
            ) + text
        if data.get("forced"):
            text = "Закреплён · " + text
        endpoint = data.get("endpoint", "")
        if endpoint:
            text += " · " + endpoint
        text = painter.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(0, int(rect.width() - flag_width - 98))
        )
        painter.drawText(QPointF(left, rect.top() + 42), text)
        painter.restore()


class NodeList(QListWidget):
    pin_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("nodeCards")
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setItemDelegate(NodeDelegate(self))
        self.setUniformItemSizes(True)
        self.setMinimumHeight(130)
        self.setMouseTracking(True)
        self.pin_enabled = True
        self._pressed_pin = None

    def pin_rect(self, item):
        rect = self.visualItemRect(item)
        return QRectF(rect.left() + 4, rect.top(), 30, rect.height())

    def request_pin(self, item):
        data = item.data(Qt.ItemDataRole.UserRole + 1) or {}
        if self.pin_enabled and (
            data.get("forced") or data.get("can_force") and not data.get("frozen")
        ):
            self.setCurrentItem(item)
            self.pin_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if (
            event.button() == Qt.MouseButton.LeftButton
            and item
            and self.pin_rect(item).contains(event.position())
        ):
            self._pressed_pin = item.data(Qt.ItemDataRole.UserRole)
            self.setCurrentItem(item)
            event.accept()
            return
        self._pressed_pin = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        member, self._pressed_pin = self._pressed_pin, None
        if member is not None and event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if (
                item
                and item.data(Qt.ItemDataRole.UserRole) == member
                and self.pin_rect(item).contains(event.position())
            ):
                self.request_pin(item)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and self.currentItem():
            self.request_pin(self.currentItem())
            event.accept()
            return
        super().keyPressEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            item = self.itemAt(event.pos())
            if item and self.pin_rect(item).contains(QPointF(event.pos())):
                data = item.data(Qt.ItemDataRole.UserRole + 1) or {}
                text = (
                    "Вернуть стратегию подписки"
                    if data.get("forced")
                    else "Закрепить маршрут независимо от стратегии · Пробел"
                )
                if data.get("frozen") and not data.get("forced"):
                    text = "Маршрут на паузе. Дождитесь успешной проверки."
                QToolTip.showText(event.globalPos(), text, self)
                return True
        return super().viewportEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setGridSize(QSize(max(200, self.viewport().width() - 2), 54))
