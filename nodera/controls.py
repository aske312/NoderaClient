"""Consistent selectors for pages and dialogs."""

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPolygonF, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QListView,
    QProxyStyle,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleFactory,
    QStyleOptionComboBox,
    QStyleOptionSpinBox,
)


class SpinArrows:
    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                QColor("#91b6c1" if self.isEnabled() else "#45606d"),
                1.3,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        for control, direction in (
            (QStyle.SubControl.SC_SpinBoxUp, -1),
            (QStyle.SubControl.SC_SpinBoxDown, 1),
        ):
            rect = self.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox, option, control, self
            )
            x, y = rect.center().x(), rect.center().y()
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(x - 3, y - direction),
                        QPointF(x, y + direction * 2),
                        QPointF(x + 3, y - direction),
                    ]
                )
            )


class NumberBox(SpinArrows, QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lineEdit().setTextMargins(8, 0, 0, 0)


class CoordinateBox(SpinArrows, QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lineEdit().setTextMargins(8, 0, 0, 0)


class Switch(QCheckBox):
    def __init__(self, caption="", parent=None):
        super().__init__(parent)
        self.setAccessibleName(caption)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 24)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else 0.4)
        painter.setPen(
            QPen(
                QColor(
                    "#8addd3" if self.hasFocus() else "#41716e" if self.isChecked() else "#35515e"
                ),
                1,
            )
        )
        painter.setBrush(QColor("#397d76" if self.isChecked() else "#19313e"))
        painter.drawRoundedRect(QRectF(1, 3, 38, 18), 9, 9)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#c4f4e7" if self.isChecked() else "#819da8"))
        painter.drawEllipse(QPointF(29 if self.isChecked() else 11, 12), 6, 6)


class ChoiceDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 48, max(32, size.height()))

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        active = bool(
            option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        )
        current = index.row() == self.parent().currentIndex()
        rect = option.rect.adjusted(2, 1, -2, -1)
        if active and enabled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#1e414b"))
            painter.drawRoundedRect(rect, 6, 6)
        elif current:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#16313b"))
            painter.drawRoundedRect(rect, 6, 6)
        painter.setFont(option.font)
        painter.setPen(QColor("#e4eff2" if enabled else "#587480"))
        text_rect = rect.adjusted(12, 0, -36, 0)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, rect.left() + 12, rect.center().y() - 8, 22, 16)
            text_rect.adjust(30, 0, 0, 0)
        text = option.fontMetrics.elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
            Qt.TextElideMode.ElideRight,
            max(0, text_rect.width()),
        )
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text
        )
        if current:
            x, y = rect.right() - 18, rect.center().y()
            painter.setPen(
                QPen(
                    QColor("#86d7ca"),
                    1.8,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawPolyline(
                QPolygonF([QPointF(x - 4, y), QPointF(x - 1, y + 3), QPointF(x + 5, y - 4)])
            )
        painter.restore()


class SelectorStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0
        return super().styleHint(hint, option, widget, returnData)


class ComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._selector_style = SelectorStyle(QStyleFactory.create("Fusion"))
        self._selector_style.setParent(self)
        self.setStyle(self._selector_style)
        view = QListView(self)
        view.setObjectName("comboPopup")
        view.setFont(QApplication.font())
        view.setMouseTracking(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setView(view)
        popup = view.window()
        if popup is not self.window():
            popup.setObjectName("comboPopupFrame")
            popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setItemDelegate(ChoiceDelegate(self))
        self.setMaxVisibleItems(9)

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, self
        )
        x, y = rect.center().x(), rect.center().y()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                QColor("#8fb7c0" if self.isEnabled() else "#435f6d"),
                1.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        direction = -1 if self.view().isVisible() else 1
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(x - 4, y - direction * 2),
                    QPointF(x, y + direction * 2),
                    QPointF(x + 4, y - direction * 2),
                ]
            )
        )
        painter.end()

    def showPopup(self):
        if not self.count():
            return
        metrics = self.view().fontMetrics()
        width = max(
            (metrics.horizontalAdvance(self.itemText(i)) + 64 for i in range(self.count())),
            default=self.width(),
        )
        available = self.screen().availableGeometry().width() - 24
        self.view().setMinimumWidth(min(max(self.width(), width), available))
        super().showPopup()
        popup = self.view().window()
        if isinstance(popup, QFrame):
            popup.setFrameShape(QFrame.Shape.NoFrame)
        screen = self.screen().availableGeometry()
        below = self.mapToGlobal(QPoint(0, self.height() + 5))
        above = self.mapToGlobal(QPoint(0, -popup.height() - 5))
        y = below.y() if below.y() + popup.height() <= screen.bottom() else above.y()
        x = min(max(below.x(), screen.left() + 6), screen.right() - popup.width() - 6)
        popup.move(x, max(screen.top() + 6, y))
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(popup.rect()), 7, 7)
        popup.setMask(QRegion(outline.toFillPolygon().toPolygon()))
        self.update()

    def hidePopup(self):
        super().hidePopup()
        self.update()

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()
