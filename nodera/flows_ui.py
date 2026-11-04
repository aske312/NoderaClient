"""Expandable application cards backed by virtualized connection rows."""

import ctypes
import ntpath
import os
from functools import lru_cache

from PySide6.QtCore import QFileInfo, QPointF, QRectF, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

from nodera.flow_monitor import grouped_flows
from nodera.profile_view import format_bytes
from nodera.widgets import label, line_icon

PAYLOAD = Qt.ItemDataRole.UserRole
IDENTITY = Qt.ItemDataRole.UserRole + 1


@lru_cache(maxsize=256)
def process_icon(path):
    drive, _ = ntpath.splitdrive(path)
    if (
        os.name == "nt"
        and len(drive) == 2
        and drive[1] == ":"
        and ntpath.isabs(path)
        and path.lower().endswith(".exe")
        and ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") in {2, 3, 6}
    ):
        icon = QFileIconProvider().icon(QFileInfo(path))
        if not icon.isNull():
            return icon
    return line_icon("connections")


def target_text(row):
    target = row["site"]
    if ":" in target:
        target = "[" + target + "]"
    return target + (":" + row["port"] if row["port"] else "")


class ConnectionDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180, 60 if (index.data(PAYLOAD) or {}).get("kind") == "group" else 46)

    def paint(self, painter, option, index):
        row = index.data(PAYLOAD) or {}
        if not row:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        group = row["kind"] == "group"
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = QRectF(option.rect).adjusted(1, 3, -3, -3)
        painter.setPen(
            QPen(QColor("#467780" if selected else "#24414e" if group else "#1a3540"), 1)
        )
        painter.setBrush(QColor("#15343e" if selected else "#102732" if group else "#0b202a"))
        painter.drawRoundedRect(rect, 10 if group else 8, 10 if group else 8)
        font = option.font
        font.setPixelSize(12 if group else 11)
        font.setBold(group)
        painter.setFont(font)
        left = rect.left() + 14
        if group:
            icon = (
                process_icon(row["path"]) if row["group_type"] == "process" else line_icon("sites")
            )
            icon.paint(painter, int(left), int(rect.top() + 13), 26, 26)
            left += 36
            title = row["name"]
            detail = f"{row['active']} активных · {row['count'] - row['active']} завершённых · " + (
                f"{len(row['sites'])} сайтов/IP"
                if row["group_type"] == "process"
                else f"{len(row['processes'])} приложений"
            )
            x, y = rect.right() - 18, rect.center().y()
            direction = -1 if self.parent().isExpanded(index) else 1
            painter.setPen(
                QPen(
                    QColor("#8bb7c0"),
                    1.7,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(x - 4, y - direction * 2),
                        QPointF(x, y + direction * 2),
                        QPointF(x + 4, y - direction * 2),
                    ]
                )
            )
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#70c9aa" if row["active"] else "#4e6a78"))
            painter.drawEllipse(QPointF(left + 2, rect.top() + 14), 2.5, 2.5)
            left += 12
            title = target_text(row)
            if row["context"] != "process":
                title = row["process"] + " · " + title
            detail = " · ".join(
                filter(None, [row["network"], row["route"] or "Маршрут не сообщён", row["rule"]])
            )
        width = max(30, rect.right() - left - (205 if group else 180))
        title = painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(width))
        painter.setPen(QColor("#e0edf0" if row["active"] else "#849eab"))
        painter.drawText(QPointF(left, rect.top() + (21 if group else 18)), title)
        font.setPixelSize(10)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#7f9fac"))
        detail = painter.fontMetrics().elidedText(
            detail, Qt.TextElideMode.ElideRight, int(width + (0 if group else 18))
        )
        painter.drawText(QPointF(left, rect.top() + (39 if group else 34)), detail)
        right = rect.right() - (38 if group else 14)
        traffic = "↓ " + format_bytes(row["download"]) + "   ↑ " + format_bytes(row["upload"])
        painter.setPen(QColor("#a1d3d4" if row["active"] else "#6d8a98"))
        painter.drawText(
            QPointF(
                right - painter.fontMetrics().horizontalAdvance(traffic),
                rect.top() + (21 if group else 18),
            ),
            traffic,
        )
        caption = ("↓ " + format_bytes(row["down_rate"]) + "/с") if row["active"] else "Завершено"
        if not group and row["active"]:
            caption = "Активно · " + caption
        if group and not row["active"]:
            caption = "Недавние соединения"
        painter.setPen(QColor("#6ebcae" if row["active"] else "#617e8d"))
        painter.drawText(
            QPointF(
                right - painter.fontMetrics().horizontalAdvance(caption),
                rect.top() + (39 if group else 34),
            ),
            caption,
        )
        painter.restore()


class ConnectionBlocks(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("connectionBlocks")
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.header().setStretchLastSection(True)
        self.setRootIsDecorated(False)
        self.setIndentation(18)
        self.setExpandsOnDoubleClick(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setItemDelegate(ConnectionDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.expanded_keys = set()
        self.itemClicked.connect(self.toggle_block)
        self.itemExpanded.connect(lambda item: self.expanded_keys.add(item.data(0, IDENTITY)))
        self.itemCollapsed.connect(lambda item: self.expanded_keys.discard(item.data(0, IDENTITY)))

    def toggle_block(self, item, column=0):
        if (item.data(0, PAYLOAD) or {}).get("kind") == "group":
            item.setExpanded(not item.isExpanded())

    def keyPressEvent(self, event):
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}
            and self.currentItem()
        ):
            if (self.currentItem().data(0, PAYLOAD) or {}).get("kind") == "group":
                self.toggle_block(self.currentItem())
                return
        super().keyPressEvent(event)


def flow_tooltip(row):
    return "\n".join(
        filter(
            None,
            [
                row["process"],
                row["path"],
                target_text(row),
                "IP: " + row["ip"],
                "Маршрут: " + row["route"],
                "Правило: " + row["rule"],
                "PID: " + (row["pid"] or "не сообщён"),
                "↑ " + format_bytes(row["up_rate"]) + "/с",
                "Активно" if row["active"] else "Завершено",
            ],
        )
    )


def set_block(item, payload, identity):
    item.setData(0, PAYLOAD, payload)
    item.setData(0, IDENTITY, identity)
    if payload["kind"] == "group":
        item.setText(
            0, payload["name"] + f" · {payload['active']} активных · {payload['count']} соединений"
        )
        item.setToolTip(
            0,
            "\n".join(
                filter(
                    None,
                    [
                        payload["name"],
                        payload["path"],
                        "Сайты: " + ", ".join(sorted(payload["sites"])),
                        "Нажмите, чтобы раскрыть соединения",
                    ],
                )
            ),
        )
    else:
        item.setText(
            0,
            payload["process"]
            + " · "
            + target_text(payload)
            + (" · Активно" if payload["active"] else " · Завершено"),
        )
        item.setToolTip(0, flow_tooltip(payload))


class FlowsMixin:
    def build_flows(self):
        layout = self.page("Соединения")
        toolbar = QHBoxLayout()
        self.flow_search = QLineEdit()
        self.flow_search.setPlaceholderText("Приложение, домен, IP или маршрут")
        self.flow_search.setClearButtonEnabled(True)
        self.flow_active_only = QCheckBox("Только активные")
        self.flow_browsers_only = QCheckBox("Браузеры")
        self.flow_pause = QCheckBox("Пауза")
        toolbar.addWidget(self.flow_search, 1)
        for control in (self.flow_active_only, self.flow_browsers_only, self.flow_pause):
            toolbar.addWidget(control)
        layout.addLayout(toolbar)
        self.flow_tabs = QTabWidget()
        self.flow_views = []
        for title in ("Приложения", "Сайты", "Все соединения"):
            view = ConnectionBlocks()
            view.setAccessibleName(title + " · раскрывающиеся блоки соединений")
            self.flow_tabs.addTab(view, title)
            self.flow_views.append(view)
        layout.addWidget(self.flow_tabs, 1)
        footer = QHBoxLayout()
        self.flow_summary = label("Подключитесь, чтобы видеть соединения Nodera.", "muted", True)
        footer.addWidget(self.flow_summary, 1)
        collapse = QPushButton("Свернуть все")
        collapse.clicked.connect(
            lambda: self.flow_views[self.flow_tabs.currentIndex()].collapseAll()
        )
        copy = QPushButton("Копировать")
        copy.clicked.connect(self.copy_flows)
        clear = QPushButton("Очистить недавние")
        clear.clicked.connect(self.clear_recent_flows)
        self.close_flows_button = QPushButton("Отключить выбранное")
        self.close_flows_button.clicked.connect(self.close_selected_flows)
        for view in self.flow_views:
            view.itemSelectionChanged.connect(self.refresh_flow_controls)
        for button in (self.close_flows_button, collapse, copy, clear):
            footer.addWidget(button)
        layout.addLayout(footer)
        self.flow_note = label("", "muted", True)
        layout.addWidget(self.flow_note)
        for signal in (
            self.flow_search.textChanged,
            self.flow_active_only.toggled,
            self.flow_browsers_only.toggled,
            self.flow_pause.toggled,
            self.flow_tabs.currentChanged,
        ):
            signal.connect(lambda *_: self.render_flows(force=True))

    def selected_flow_ids(self):
        ids = set()
        view = self.flow_views[self.flow_tabs.currentIndex()]
        for item in view.selectedItems():
            rows = (
                [item]
                if (item.data(0, PAYLOAD) or {}).get("kind") == "flow"
                else [item.child(i) for i in range(item.childCount())]
            )
            for child in rows:
                row = child.data(0, PAYLOAD) or {}
                if row.get("active") and row.get("id"):
                    ids.add(row["id"])
        return sorted(ids)

    def refresh_flow_controls(self):
        if hasattr(self, "close_flows_button"):
            self.close_flows_button.setEnabled(
                bool(
                    self.connected
                    and not self.task
                    and not self.closing
                    and self.selected_flow_ids()
                )
            )

    def close_selected_flows(self):
        identities = self.selected_flow_ids()
        if not self.connected or self.task or self.closing or not identities:
            return

        def done(result):
            closed, failed = result
            for identity in closed:
                row = self.flow_monitor.flows.get(identity)
                if row:
                    row.update(active=False, up_rate=0, down_rate=0)
            self.render_flows(force=True)
            self.log(
                f"Отключено соединений: {len(closed)}"
                + (f" · Не удалось: {failed}" if failed else "")
            )

        self.run_task(lambda: self.core.close_connections(identities), done)

    def clear_recent_flows(self):
        self.flow_monitor.flows = {
            key: row for key, row in self.flow_monitor.flows.items() if row["active"]
        }
        self.render_flows(force=True)

    def copy_flows(self):
        items = self.flow_views[self.flow_tabs.currentIndex()].selectedItems()
        if items:
            QApplication.clipboard().setText("\n\n".join(item.toolTip(0) for item in items))

    def render_flows(self, force=False):
        if hasattr(self, "flow_note"):
            self.flow_note.setText(
                f"Домены/IP из ядра, без названий вкладок. Завершённые — {self.settings.flow_history_minutes} мин в памяти."
            )
        if not hasattr(self, "flow_views") or (self.flow_pause.isChecked() and not force):
            return
        if self.stack.currentIndex() != 8 and not force:
            return
        rows = self.flow_monitor.rows(
            query=self.flow_search.text().strip(),
            active_only=self.flow_active_only.isChecked(),
            browsers_only=self.flow_browsers_only.isChecked(),
        )
        tab = self.flow_tabs.currentIndex()
        kind = "process" if tab == 0 else "site"
        groups = grouped_flows(rows, kind) if tab < 2 else []
        groups.sort(key=lambda group: (not bool(group["active"]), group["name"].casefold()))
        by_group = {}
        for row in rows:
            key = (row["path"] or row["process"]).casefold() if tab == 0 else row["site"]
            by_group.setdefault(key, []).append(row)
        view = self.flow_views[tab]
        scroll = view.verticalScrollBar().value()
        selected = view.currentItem()
        selected_key = selected.data(0, IDENTITY) if selected else None
        blocker = QSignalBlocker(view)
        view.setUpdatesEnabled(False)
        existing = {
            view.topLevelItem(index).data(0, IDENTITY): view.topLevelItem(index)
            for index in range(view.topLevelItemCount())
        }
        desired = (
            [("group:" + group["key"], group) for group in groups]
            if tab < 2
            else [
                ("flow:" + row["id"], row)
                for row in sorted(
                    rows, key=lambda row: (not row["active"], -row["seen"], row["process"])
                )
            ]
        )
        keep = {key for key, _ in desired}
        for key, item in existing.items():
            if key not in keep:
                view.takeTopLevelItem(view.indexOfTopLevelItem(item))
        selected_item = None
        for position, (key, row) in enumerate(desired):
            item = existing.get(key)
            if item is None:
                item = QTreeWidgetItem()
                view.insertTopLevelItem(position, item)
            elif view.indexOfTopLevelItem(item) != position:
                view.takeTopLevelItem(view.indexOfTopLevelItem(item))
                view.insertTopLevelItem(position, item)
            if tab == 2:
                set_block(item, {**row, "kind": "flow", "context": "all"}, key)
            else:
                set_block(item, {**row, "kind": "group", "group_type": kind}, key)
                children = {
                    item.child(index).data(0, IDENTITY): item.child(index)
                    for index in range(item.childCount())
                }
                members = sorted(
                    by_group[row["key"]],
                    key=lambda entry: (not entry["active"], entry["site"], entry["id"]),
                )
                member_keys = {"flow:" + member["id"] for member in members}
                for child_key, child in children.items():
                    if child_key not in member_keys:
                        item.takeChild(item.indexOfChild(child))
                for child_position, member in enumerate(members):
                    child_key = "flow:" + member["id"]
                    child = children.get(child_key)
                    if child is None:
                        child = QTreeWidgetItem()
                        item.insertChild(child_position, child)
                    elif item.indexOfChild(child) != child_position:
                        item.takeChild(item.indexOfChild(child))
                        item.insertChild(child_position, child)
                    set_block(child, {**member, "kind": "flow", "context": kind}, child_key)
                    if child_key == selected_key:
                        selected_item = child
                item.setExpanded(key in view.expanded_keys)
            if key == selected_key:
                selected_item = item
        if selected_item:
            view.setCurrentItem(selected_item)
        view.verticalScrollBar().setValue(scroll)
        view.setUpdatesEnabled(True)
        del blocker
        self.refresh_flow_controls()
        active = sum(row["active"] for row in rows)
        self.flow_summary.setText(
            (
                "Подключитесь, чтобы видеть соединения Nodera."
                if not self.connected
                else f"Активных: {active} · Завершённых: {len(rows) - active} · Приложений: {len(grouped_flows(rows, 'process'))}"
            )
            + (" · Предел 5000 записей" if self.flow_monitor.truncated else "")
        )
