import json
import math
import time
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import httpx
import yaml

from nodera import __version__
from nodera.bundled_core import core_manifest
from nodera.device_info import windows_identity
from nodera.journal import redact
from nodera.models import ClientError, Profile
from nodera.pro_provenance import new_nonce, verification_record, verify_proof

MAX_CONFIG_BYTES = 4 * 1024 * 1024

ACCESS_MESSAGES = {
    "missing": "Подписка удалена или ссылка недействительна. Получите новую ссылку у поставщика.",
    "expired": "Срок подписки истёк. Продлите её и нажмите «Обновить».",
    "exhausted": "Лимит трафика подписки исчерпан. Пополните его и нажмите «Обновить».",
    "denied": "Доступ к подписке ограничен. Проверьте срок, лимит и привязку устройства у поставщика.",
}


class SubscriptionUnavailable(ClientError):
    def __init__(self, state):
        self.state = state
        super().__init__(ACCESS_MESSAGES[state])


def subscription_rejection(response):
    if response.status_code in {404, 410}:
        return SubscriptionUnavailable("missing")
    if response.status_code != 403:
        return None
    # Only recognize known reasons; never display an arbitrary server error body.
    body = bytearray()
    for chunk in response.iter_bytes(chunk_size=1024):
        body.extend(chunk)
        if len(body) > 4096:
            return SubscriptionUnavailable("denied")
    try:
        data = json.loads(body)
        detail = data.get("detail") if isinstance(data, dict) else None
    except (ValueError, UnicodeError):
        detail = None
    state = (
        {
            "Срок подписки истёк": "expired",
            "Subscription expired": "expired",
            "Объём трафика подписки исчерпан": "exhausted",
            "Subscription traffic quota exhausted": "exhausted",
        }.get(detail, "denied")
        if isinstance(detail, str)
        else "denied"
    )
    return SubscriptionUnavailable(state)


def normalized_yaml(value, active=None, budget=None, depth=0):
    active = set() if active is None else active
    budget = [250000] if budget is None else budget
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        raise ClientError("Конфигурация слишком сложная или содержит чрезмерное число YAML-ссылок.")
    if isinstance(value, (dict, list)):
        key = id(value)
        if key in active:
            raise ClientError("Конфигурация содержит циклические YAML-ссылки.")
        active.add(key)
        try:
            if isinstance(value, list):
                return [normalized_yaml(item, active, budget, depth + 1) for item in value]
            if any(not isinstance(name, str) for name in value):
                raise ClientError("Ключи конфигурации должны быть текстовыми.")
            for name in value:
                name.encode("utf-8")
            return {
                name: normalized_yaml(item, active, budget, depth + 1)
                for name, item in value.items()
            }
        finally:
            active.remove(key)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        value.encode("utf-8")
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ClientError("Конфигурация содержит неподдерживаемые значения YAML.")


def validate_url(url: str) -> str:
    url = url.strip()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ClientError("Некорректный адрес подписки.") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port == 0
        or any(character.isspace() for character in url)
    ):
        raise ClientError("Укажите HTTPS-ссылку подписки Mihomo, без логина и пароля.")
    return url


def parse_config(content: bytes) -> dict:
    if len(content) > MAX_CONFIG_BYTES:
        raise ClientError("Конфигурация превышает допустимые 4 МБ.")
    try:
        config = normalized_yaml(yaml.safe_load(content.decode("utf-8-sig")))
    except (UnicodeError, yaml.YAMLError, RecursionError, ValueError) as exc:
        raise ClientError("Сервер вернул некорректный Mihomo YAML.") from exc
    if not isinstance(config, dict):
        raise ClientError("Ожидается конфигурация Mihomo YAML, а не страница панели.")
    proxies = config.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise ClientError("В профиле нет подключений. Проверьте подписку у поставщика подписки.")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("name"), str)
        or not item.get("name")
        or not isinstance(item.get("type"), str)
        or not item.get("type")
        for item in proxies
    ):
        raise ClientError("Некорректный список подключений Mihomo.")
    groups = config.get("proxy-groups", [])
    if not isinstance(groups, list) or any(
        not isinstance(group, dict)
        or not isinstance(group.get("name"), str)
        or not group.get("name")
        or not isinstance(group.get("type"), str)
        or not isinstance(group.get("proxies", []), list)
        or any(not isinstance(name, str) for name in group.get("proxies", []))
        for group in groups
    ):
        raise ClientError("Некорректные группы серверов Mihomo.")
    names = [item["name"] for item in proxies] + [group["name"] for group in groups]
    if len(names) != len(set(names)):
        raise ClientError("Имена серверов и групп в профиле должны быть уникальными.")
    for key in ("dns", "tun"):
        if key in config and not isinstance(config[key], dict):
            raise ClientError("Некорректные настройки DNS или TUN в профиле.")
    rules = config.get("rules", [])
    if not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules):
        raise ClientError("Некорректный список правил в подписке.")
    if "mode" in config and not isinstance(config["mode"], str):
        raise ClientError("Некорректный режим маршрутизации в подписке.")
    for key in ("proxy-providers", "rule-providers"):
        providers = config.get(key, {})
        if not isinstance(providers, dict):
            raise ClientError("Некорректные источники правил или подключений.")
        for provider in providers.values():
            if not isinstance(provider, dict) or provider.get("type") != "http":
                raise ClientError("Поддерживаются только HTTP-источники правил и подключений.")
            validate_url(str(provider.get("url", "")))
    return config


def fetch_profile(
    url: str,
    name: str,
    device_id: str,
    transport: httpx.BaseTransport | None = None,
    *,
    proxy=None,
    log=None,
    expected_public_key=None,
    core_version=None,
) -> Profile:
    url = validate_url(url)
    started = time.monotonic()
    phase = "connect"
    emit = log or (lambda message, level="info": None)
    emit(
        f"event=subscription_fetch_started host={urlsplit(url).hostname} via_proxy={proxy is not None}"
    )
    device_name, os_version = windows_identity()
    nonce = new_nonce()
    headers = {
        "Accept": "text/yaml, application/yaml, text/plain",
        "User-Agent": f"Nodera/{__version__} (Windows; Mihomo)",
        "X-Device-ID": device_id,
        "X-Device-Name": device_name,
        "X-Device-OS": "windows",
        "X-OS-Version": os_version,
        "X-Client-Name": "Nodera",
        "X-Client-Version": __version__,
        "X-Core-Name": "mihomo",
        "X-Core-Version": core_version or core_manifest()["version"].removeprefix("v"),
        "X-Subscription-Nonce": nonce,
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            proxy=proxy,
        ) as client:
            with client.stream("GET", url, headers=headers) as response:
                phase = "response"
                emit(
                    f"event=subscription_response status={response.status_code} elapsed_ms={int((time.monotonic() - started) * 1000)}"
                )
                rejection = subscription_rejection(response)
                if rejection:
                    raise rejection
                if response.is_redirect:
                    raise ClientError(
                        "Подписка перенаправляет запрос. Используйте конечный HTTPS-адрес."
                    )
                response.raise_for_status()
                if response.headers.get("x-nodera-selection-state") == "pending":
                    raise ClientError(
                        "На сервере подготовлен профиль, ожидающий решения. Откройте «Опции» подписки. Текущая подписка сохранена."
                    )
                phase = "body"
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_CONFIG_BYTES:
                        raise ClientError("Конфигурация превышает допустимые 4 МБ.")
                phase = "parse"
                emit(f"event=subscription_body_received bytes={len(content)}")
                claims = None
                proof = response.headers.get("x-subscription-proof", "")
                public_key = response.headers.get("x-subscription-public-key")
                if proof:
                    try:
                        claims = verify_proof(
                            proof,
                            bytes(content),
                            str(response.url),
                            nonce,
                            public_key=public_key,
                            expected_public_key=expected_public_key,
                        )
                    except ValueError:
                        emit("event=subscription_pro_verification state=invalid", "warning")
                    else:
                        emit("event=subscription_pro_verification state=verified")
                if expected_public_key is not None and claims is None:
                    raise ClientError(
                        "Не удалось подтвердить прежний ключ сервера подписки. Текущая подписка сохранена."
                    )
                config = parse_config(bytes(content))
                pro_verification = (
                    verification_record(
                        claims, config, public_key=public_key or expected_public_key
                    )
                    if claims
                    else {}
                )
                usage = {}
                for part in response.headers.get("subscription-userinfo", "").split(";"):
                    key, _, value = part.strip().partition("=")
                    if (
                        key in {"upload", "download", "total", "expire"}
                        and value.isdigit()
                        and len(value) <= 20
                    ):
                        usage[key] = int(value)
                emit(
                    f"event=subscription_parsed servers={len(config['proxies'])} elapsed_ms={int((time.monotonic() - started) * 1000)}"
                )
    except ClientError:
        emit(f"event=subscription_failed phase={phase} type=ClientError", "warning")
        raise
    except httpx.TimeoutException as exc:
        emit(
            f"event=subscription_failed phase={phase} type={type(exc).__name__} detail={redact(str(exc), (url,))}",
            "warning",
        )
        raise ClientError(
            "Сервер подписки не ответил вовремя. Сохранённая подписка не изменена."
        ) from exc
    except httpx.HTTPStatusError as exc:
        emit(
            f"event=subscription_failed phase={phase} status={exc.response.status_code}", "warning"
        )
        raise ClientError(
            f"Сервер подписки вернул HTTP {exc.response.status_code}. Сохранённая подписка не изменена."
        ) from exc
    except httpx.DecodingError as exc:
        emit(f"event=subscription_failed phase={phase} type=DecodingError", "warning")
        raise ClientError(
            "Сервер вернул повреждённый сжатый ответ подписки. Прежняя подписка сохранена."
        ) from exc
    except httpx.HTTPError as exc:
        emit(
            f"event=subscription_failed phase={phase} type={type(exc).__name__} detail={redact(str(exc), (url,))}",
            "warning",
        )
        raise ClientError(
            "Не удалось загрузить подписку. Проверьте сеть, TLS и доступность сервера."
        ) from exc
    return Profile(
        name.strip()[:80] or "Моя подписка",
        url,
        config,
        datetime.now(timezone.utc).isoformat(),
        usage,
        pro_verification=pro_verification,
    )
