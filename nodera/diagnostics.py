"""Local preflight checks; no subscription secrets or raw core output in reports."""

import errno
import ipaddress
import socket

from nodera.bundled_core import core_manifest, resolve_core
from nodera.core import is_admin
from nodera.core_updates import CoreUpdates
from nodera.models import ClientError, Profile, Settings, visible_proxies
from nodera.ports import choose_ports
from nodera.profile_view import effective_tun


def ipv6_route_available():
    """Ask Windows to select a source/route; a UDP connect sends no payload."""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as stream:
            stream.connect(("2001:4860:4860::8888", 53))
            source = ipaddress.ip_address(stream.getsockname()[0].split("%")[0])
            return not (source.is_unspecified or source.is_loopback or source.is_link_local)
    except OSError as exc:
        if exc.errno in {
            errno.ENETUNREACH,
            errno.EHOSTUNREACH,
            errno.EADDRNOTAVAIL,
            errno.EAFNOSUPPORT,
        } or getattr(exc, "winerror", None) in {10049, 10051, 10065, 10047}:
            return False
        return None


def ipv6_route_check(enabled):
    if not enabled:
        return None
    available = ipv6_route_available()
    if available is False:
        return {
            "title": "IPv6 на компьютере",
            "state": "warning",
            "detail": "IPv6 включён в сеансе, но Windows не может выбрать внешний IPv6-маршрут. Прямые IPv6-подключения и DNS могут давать ошибки. Проверьте сеть или отключите IPv6 в настройках Nodera.",
        }
    return {
        "title": "IPv6 на компьютере",
        "state": "ok" if available else "info",
        "detail": "Windows выбрала IPv6-маршрут. Доступность сайтов проверяется отдельно."
        if available
        else "Не удалось проверить IPv6-маршрут Windows. Настройки не изменены.",
    }


def preflight(
    settings: Settings, profile: Profile | None, running: bool = False, root=None
) -> list[dict]:
    checks = []

    def add(title, state, detail):
        checks.append({"title": title, "state": state, "detail": detail})

    try:
        if root is None:
            resolve_core()
            version = core_manifest()["version"]
        else:
            manager = CoreUpdates(root)
            manager.resolve()
            version = manager.version()
        add("Ядро Mihomo", "ok", f"Mihomo {version}. Целостность проверена.")
    except ClientError as exc:
        add("Ядро Mihomo", "error", str(exc))
    if profile and profile.config.get("proxies"):
        add("Подписка", "ok", f"Доступно подключений: {len(visible_proxies(profile.config))}.")
    else:
        add("Подписка", "error", "Добавьте HTTPS-ссылку подписки в разделе «Подписки».")
    if not effective_tun(profile.config if profile else {}, settings):
        add(
            "Режим подключения",
            "ok",
            "HTTP/SOCKS5 с автоматическим выбором порта. Системный прокси настраивается при подключении."
            if settings.system_proxy
            else "HTTP/SOCKS5. Фактический адрес будет показан после подключения.",
        )
    elif is_admin():
        add("Права для TUN", "ok", "Права администратора доступны. Маршруты задаёт подписка.")
    else:
        add(
            "Права для TUN",
            "info",
            "При подключении TUN Nodera запросит права Windows. Для Proxy они не нужны.",
        )
    if running:
        add("Локальный порт", "ok", "Используется текущим сеансом Nodera.")
    else:
        try:
            selected, _ = choose_ports(settings.mixed_port)
            add(
                "Локальный порт",
                "ok",
                f"Автоматический выбор доступен: {selected}. Занятые порты пропускаются.",
            )
        except ClientError:
            add(
                "Локальный порт",
                "error",
                "Windows не предоставила локальные порты. Проверьте системные сетевые ограничения.",
            )
    add(
        "Проверка сети",
        "info",
        "После запуска ядра выберите узел в разделе «Серверы» и выполните HTTPS-проверку.",
    )
    return checks
