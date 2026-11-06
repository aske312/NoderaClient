"""Local preflight checks; no subscription secrets or raw core output in reports."""

from nodera.bundled_core import core_manifest, resolve_core
from nodera.core import is_admin
from nodera.models import ClientError, Profile, Settings, visible_proxies
from nodera.ports import choose_ports
from nodera.profile_view import effective_tun


def preflight(settings: Settings, profile: Profile | None, running: bool = False) -> list[dict]:
    checks = []

    def add(title, state, detail):
        checks.append({"title": title, "state": state, "detail": detail})

    try:
        resolve_core()
        add("Ядро Mihomo", "ok", f"Встроенное {core_manifest()['version']}. Целостность проверена.")
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
