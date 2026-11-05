"""Floating navigation dock with a small secondary page palette."""

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QToolButton, QVBoxLayout

from nodera.widgets import label, line_icon


class NavigationDock(QFrame):
    selected = Signal(int)

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("navigationDock")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)
        self.buttons = {}
        self.shortcuts = []
        self.pages = {
            0: ("Главная", "home"),
            4: ("Серверы", "servers"),
            1: ("Подписки", "profile"),
            6: ("Правила", "routes"),
            8: ("Соединения", "connections"),
            2: ("Настройки", "settings"),
            7: ("О программе", "info"),
        }
        for shortcut, index in enumerate((0, 4, 1), 1):
            title, icon = self.pages[index]
            button = QToolButton()
            button.setObjectName("dockTab")
            button.setIcon(line_icon(icon))
            button.setIconSize(QSize(16, 16))
            button.setText(title)
            button.setAccessibleName(title)
            button.setToolTip(f"{title} · Ctrl+{shortcut}")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setFixedSize(94, 32)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page=index: self.selected.emit(page))
            self.buttons[index] = button
            layout.addWidget(button)
            key = QShortcut(QKeySequence(f"Ctrl+{shortcut}"), window)
            key.activated.connect(lambda page=index: self.selected.emit(page))
            self.shortcuts.append(key)
        divider = QFrame()
        divider.setObjectName("dockDivider")
        divider.setFixedSize(1, 18)
        layout.addSpacing(2)
        layout.addWidget(divider)
        layout.addSpacing(2)
        self.tools = QToolButton()
        self.tools.setObjectName("dockTab")
        self.tools.setProperty("tools", True)
        self.tools.setCheckable(True)
        self.tools.setFixedSize(132, 32)
        self.tools.setIconSize(QSize(16, 16))
        self.tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tools.setAccessibleName("Инструменты")
        self.tools.setToolTip("Инструменты · Ctrl+K")
        self.tools.clicked.connect(self.toggle_palette)
        chevron = label("")
        chevron.setParent(self.tools)
        chevron.setPixmap(line_icon("chevron-up").pixmap(10, 10))
        chevron.setGeometry(115, 11, 10, 10)
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.tools)
        self.palette = QFrame(window, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.palette.setObjectName("navigationPalette")
        self.palette.setFixedWidth(224)
        palette_layout = QVBoxLayout(self.palette)
        palette_layout.setContentsMargins(6, 6, 6, 6)
        palette_layout.setSpacing(2)
        descriptions = {
            6: "Маршрутизация подключений",
            8: "Активность приложений",
            2: "Параметры клиента",
            7: "Версия и обновления",
        }
        for index, description in descriptions.items():
            title, icon = self.pages[index]
            button = QPushButton()
            button.setObjectName("navigationEntry")
            button.setFixedHeight(34)
            button.setCheckable(True)
            button.setAccessibleName(title)
            button.setToolTip(description)
            row = QHBoxLayout(button)
            row.setContentsMargins(9, 5, 9, 5)
            row.setSpacing(9)
            image = label("")
            image.setPixmap(line_icon(icon).pixmap(16, 16))
            row.addWidget(image)
            row.addWidget(label(title, "navigationTitle"), 1)
            for child in button.findChildren(type(image)):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            button.clicked.connect(lambda checked=False, page=index: self.choose(page))
            self.buttons[index] = button
            palette_layout.addWidget(button)
        key = QShortcut(QKeySequence("Ctrl+K"), window)
        key.activated.connect(self.toggle_palette)
        self.shortcuts.append(key)
        self.current_index = 0
        self.set_current(0)

    def choose(self, index):
        self.palette.hide()
        self.selected.emit(index)

    def set_current(self, index):
        self.current_index = index
        for page, button in self.buttons.items():
            button.setChecked(page == index)
        secondary = index in (6, 8, 2, 7)
        title, icon = self.pages[index] if secondary else ("Инструменты", "settings")
        self.tools.setText(title)
        self.tools.setIcon(line_icon(icon))
        self.tools.setChecked(secondary)

    def toggle_palette(self):
        # The active state describes the page, independently of popup visibility.
        self.set_current(self.current_index)
        if self.palette.isVisible():
            self.palette.hide()
            return
        self.palette.adjustSize()
        anchor = self.tools.mapToGlobal(QPoint(self.tools.width(), 0))
        screen = self.tools.screen().availableGeometry()
        x = max(
            screen.left() + 8,
            min(anchor.x() - self.palette.width(), screen.right() - self.palette.width() - 8),
        )
        y = max(screen.top() + 8, anchor.y() - self.palette.height() - 8)
        self.palette.move(x, y)
        self.palette.show()
        self.buttons[self.current_index if self.current_index in (6, 8, 2, 7) else 6].setFocus()
