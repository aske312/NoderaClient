"""Offline, scalable vector map with SVG flags; no web view or network requests."""

import html

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QTransform,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolTip, QWidget

from nodera.locations import RESOURCE, map_data
from nodera.map_projection import project_point, project_view, route_links, schematic_links


class WorldMap(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.locations = []
        self.connected = False
        self.bottom_inset = 0
        self.top_inset = 0
        self.client_point = {}
        self.auto_zoom = True
        self.setMinimumHeight(150)
        self.setAccessibleName("Карта мира · Не подключено")
        self._land = QPainterPath()
        self._flags = {}
        self._background_key = None
        self._background_layers = None
        self.hit_regions = []
        self.setMouseTracking(True)
        for ring in map_data()["land"]:
            self._land.moveTo(ring[0][0] + 180, 85 - ring[0][1])
            for lon, lat in ring[1:]:
                self._land.lineTo(lon + 180, 85 - lat)
            self._land.closeSubpath()

    def set_locations(self, locations, connected):
        if self.locations == locations and self.connected == connected:
            return
        self.locations, self.connected = locations, connected
        self.hit_regions = []
        text = " · ".join(point_name(item) for item in locations)
        state = text or ("Локация не указана" if connected else "Не подключено")
        self.setAccessibleName("Карта мира · " + state)
        self.describe()
        self.update()

    def set_client_point(self, point, auto_zoom=True):
        if self.client_point != point or self.auto_zoom != auto_zoom:
            self.client_point = dict(point)
            self.hit_regions = []
            self.auto_zoom = auto_zoom
            self.describe()
            self.update()

    def describe(self):
        lines = []
        if self.client_point:
            source = (
                "по внешнему IP, приблизительно"
                if self.client_point.get("source") == "ip-physical"
                else "указано вручную"
            )
            lines.append("Клиент: " + point_name(self.client_point) + " · " + source)
        for location in self.locations:
            role = {"relay": "Relay", "cdn": "CDN", "server": "Выход"}.get(
                location.get("role"), "Сервер"
            )
            lines.append(role + ": " + point_name(location))
        if any(not location.get("order_known", True) for location in self.locations):
            lines.append(
                "Светлый пунктир — схема известных точек; порядок участков не передан подпиской."
            )
        self.setAccessibleDescription("\n".join(lines))
        self.setToolTip("")
        if self.parentWidget():
            self.parentWidget().setToolTip("")

    def tooltip_at(self, position):
        for point, badge, location in reversed(self.hit_regions):
            if (
                badge.contains(QPointF(position))
                or (point - QPointF(position)).manhattanLength() <= 14
            ):
                return point_tooltip(location)
        return ""

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            show_point_tooltip(self, self, event)
            return True
        return super().event(event)

    def background_layers(self, view):
        ratio = self.devicePixelRatioF()
        key = (
            self.width(),
            self.height(),
            ratio,
            view["scale"],
            view["x"],
            view["y"],
            tuple(view["frame"]),
        )
        if key == self._background_key:
            return self._background_layers
        source = QImage(
            max(1, round(self.width() * ratio)),
            max(1, round(self.height() * ratio)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        source.setDevicePixelRatio(ratio)
        source.fill(Qt.GlobalColor.transparent)
        painter = QPainter(source)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = view["scale"]
        map_rect = QRectF(*view["frame"])
        painter.save()
        painter.setClipRect(QRectF(self.rect()))
        painter.setPen(QPen(QColor(97, 207, 216, 16), 0.7))
        for latitude in (-30, 0, 30, 60):
            y = view["y"] + (85 - latitude) * scale
            painter.drawLine(QPointF(map_rect.left(), y), QPointF(map_rect.right(), y))
        for longitude in (-120, -60, 0, 60, 120):
            x, _ = project_point({"lon": longitude, "lat": 0}, view)
            painter.drawLine(QPointF(x, map_rect.top()), QPointF(x, map_rect.bottom()))
        land = QPainterPath()
        for shift in (-360, 0, 360, 720):
            transform = (
                QTransform().translate(view["x"] + shift * scale, view["y"]).scale(scale, scale)
            )
            land.addPath(transform.map(self._land))
        painter.setPen(QPen(QColor("#284754"), 0.6))
        painter.setBrush(QColor("#102630"))
        painter.drawPath(land)
        painter.setClipPath(land, Qt.ClipOperation.IntersectClip)
        painter.setPen(QPen(QColor("#3b6570"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for y in range(0, self.height(), 6):
            for x in range(0, self.width(), 6):
                painter.drawPoint(QPointF(x, y))
        painter.restore()
        painter.end()
        # A low-resolution layer softens only the map; route markers are drawn later.
        blurred = source.scaled(
            max(1, source.width() // 9),
            max(1, source.height() // 9),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ).scaled(
            source.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        blurred.setDevicePixelRatio(ratio)

        def feather(layer, horizontal_inset, vertical_inset):
            mask = QPainter(layer)
            mask.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            for gradient, inset in (
                (QLinearGradient(0, 0, self.width(), 0), horizontal_inset),
                (QLinearGradient(0, 0, 0, self.height()), vertical_inset),
            ):
                for stop, alpha in ((0, 0), (inset, 255), (1 - inset, 255), (1, 0)):
                    gradient.setColorAt(stop, QColor(255, 255, 255, alpha))
                mask.fillRect(QRectF(self.rect()), gradient)
            mask.end()

        feather(blurred, 0.025, 0.04)
        feather(source, 0.10, 0.13)
        self._background_key = key
        self._background_layers = (blurred, source)
        return self._background_layers

    def paintEvent(self, event):
        self.hit_regions = []
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        locations = self.locations
        if self.client_point:
            locations = [{**self.client_point, "role": "client", "step": -1}, *locations]
        view = project_view(
            locations,
            self.width(),
            self.height(),
            self.top_inset,
            self.bottom_inset,
            self.auto_zoom and self.connected,
            cover=not self.connected,
        )
        width = 360 * view["scale"]
        map_rect = QRectF(*view["frame"])
        blurred, sharp = self.background_layers(view)
        painter.drawImage(QPointF(0, 0), blurred)
        painter.drawImage(QPointF(0, 0), sharp)
        fade = QLinearGradient(0, self.height() - self.bottom_inset - 80, 0, self.height())
        fade.setColorAt(0, QColor(9, 23, 32, 0))
        fade.setColorAt(1, QColor(9, 23, 32, 240 if self.connected else 130))
        painter.fillRect(
            QRectF(0, self.height() - self.bottom_inset - 80, self.width(), self.bottom_inset + 80),
            fade,
        )
        points = [QPointF(*project_point(item, view)) for item in locations]
        painter.save()
        painter.setClipRect(map_rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(105, 200, 170, 155), 1.3, Qt.PenStyle.DashLine))
        confirmed = route_links(locations, bool(self.client_point))
        schematic = schematic_links(locations, bool(self.client_point))
        for first, second in confirmed + schematic:
            painter.setPen(
                QPen(
                    QColor(105, 200, 170, 190 if (first, second) in confirmed else 90),
                    1.5,
                    Qt.PenStyle.DashLine if (first, second) in confirmed else Qt.PenStyle.DotLine,
                )
            )
            start, finish = points[first], points[second]
            copies = [(start, finish)]
            if abs(start.x() - finish.x()) > width / 2:
                shift = width if finish.x() < start.x() else -width
                copies = [(start, finish + QPointF(shift, 0)), (start - QPointF(shift, 0), finish)]
            for origin, target in copies:
                path = QPainterPath(origin)
                path.quadTo(
                    (origin + target) / 2 + QPointF(0, -min(30, abs(target.x() - origin.x()) / 5)),
                    target,
                )
                painter.drawPath(path)
        painter.restore()
        badges = []
        captions = []
        for index, location in enumerate(locations):
            point = points[index]
            glow = QRadialGradient(point, 22)
            glow.setColorAt(0, QColor(105, 200, 170, 115))
            glow.setColorAt(1, QColor(105, 200, 170, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(point, 22, 22)
            painter.setBrush(QColor("#091c24"))
            painter.setPen(QPen(QColor("#69c8aa"), 1.5))
            painter.drawEllipse(point, 5, 5)
            painter.setBrush(QColor("#9af0ce"))
            painter.drawEllipse(point, 2, 2)
            font = painter.font()
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            text = (
                "1 · " + point_name(location)
                if location.get("role") == "client"
                else (
                    (str(location.get("step", index) + (2 if self.client_point else 1)) + " · ")
                    if location.get("order_known", True)
                    else ""
                )
                + point_name(location)
            )
            if (
                self.client_point
                and location.get("entry")
                and not location.get("order_known", True)
            ):
                text = "2 · " + point_name(location)
            badge_width = min(
                190, self.width() - 8, painter.fontMetrics().horizontalAdvance(text) + 38
            )
            x = max(4, min(self.width() - badge_width - 4, point.x() + 12))
            lower = self.height() - self.bottom_inset - 28
            y = max(self.top_inset + 4, min(lower, point.y() - 32))
            badge = QRectF(x, y, badge_width, 22)
            for candidate_y in [
                y,
                y + 32,
                y - 32,
                *range(self.top_inset + 4, max(self.top_inset + 5, int(lower)), 30),
            ]:
                badge.moveTop(max(self.top_inset + 4, min(lower, candidate_y)))
                if not any(
                    badge.adjusted(-3, -3, 3, 3).intersects(previous) for previous in badges
                ):
                    break
            badges.append(badge)
            captions.append((location, point, badge, text))
        # Draw every leader first so later hops never cross an earlier flag or label.
        painter.setPen(QPen(QColor("#69c8aa"), 0.8))
        for location, point, badge, text in captions:
            self.hit_regions.append((point, badge, location))
            painter.drawLine(point, badge.center())
        for location, point, badge, text in captions:
            painter.setPen(QPen(QColor("#69c8aa"), 0.8))
            painter.setBrush(QColor("#102b34"))
            painter.drawRoundedRect(badge, 7, 7)
            code = location["code"]
            if code not in self._flags:
                path = RESOURCE / "flags" / f"{code.lower()}.svg"
                self._flags[code] = QSvgRenderer(
                    str(path if path.is_file() else RESOURCE / "flags/xx.svg")
                )
            painter.save()
            flag_rect = QRectF(badge.x() + 6, badge.y() + 5, 18, 12)
            flag_clip = QPainterPath()
            flag_clip.addRoundedRect(flag_rect, 2, 2)
            painter.setClipPath(flag_clip)
            self._flags[code].render(painter, flag_rect)
            painter.restore()
            painter.setPen(QColor("#dcf3eb"))
            text = painter.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, int(badge.width() - 35)
            )
            painter.drawText(badge.adjusted(29, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, text)


class WorldCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.world_map = WorldMap(self)
        self.world_map.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.world_map.top_inset = 34
        self.world_map.bottom_inset = 76
        self.setMouseTracking(True)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and hasattr(self, "world_map"):
            show_point_tooltip(self, self.world_map, event)
            return True
        return super().event(event)

    def mouseMoveEvent(self, event):
        if not self.world_map.tooltip_at(event.position().toPoint()):
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.world_map.setGeometry(self.rect())
        self.world_map.lower()


def point_name(location):
    for field in ("city", "name", "code"):
        value = " ".join(str(location.get(field) or "").split())
        if value:
            return value
    return "Адрес"


def point_tooltip(location):
    role = {"client": "Клиент", "relay": "Relay", "cdn": "CDN", "server": "Выход"}.get(
        location.get("role"), "Маршрут"
    )
    lines = [role + " · " + point_name(location)]
    if location.get("node"):
        lines.append("Маршрут: " + location["node"])
    if location.get("host"):
        lines.append("Адрес: " + location["host"])
    if location.get("ip"):
        lines.append("IP: " + location["ip"])
    if location.get("source") == "geoip":
        lines.append(
            "Наблюдается TCP-соединение ядра"
            if location.get("observed")
            else "Адрес из DNS/профиля; соединение не подтверждено"
        )
    lines.append(
        "Местоположение приблизительное"
        if location.get("source") in {"geoip", "ip-physical"}
        else "Локация из настроек / подписки"
    )
    if not location.get("order_known", True):
        lines.append("Пунктир — схема; порядок участков не передан")
    return "<br>".join(html.escape(line) for line in lines)


def show_point_tooltip(widget, world_map, event):
    text = world_map.tooltip_at(world_map.mapFrom(widget, event.pos()))
    if text:
        QToolTip.showText(event.globalPos(), text, widget)
    else:
        QToolTip.hideText()
        event.ignore()
