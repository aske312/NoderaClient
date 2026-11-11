"""Read-only PRO command and sustained, isolated client channel acceptance."""

import copy
import hashlib
import json
import secrets
import socket
import struct
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from nodera import __version__
from nodera.bundled_core import core_manifest
from nodera.channel_policy import ChannelPolicy, channel_capabilities
from nodera.client_location import physical_interface
from nodera.core import Core
from nodera.core_updates import CoreUpdates
from nodera.device_info import windows_identity
from nodera.models import ClientError, Profile, Settings, visible_proxies
from nodera.subscription import MAX_CONFIG_BYTES, parse_config, validate_url

TRANSFER_URL = "https://speed.cloudflare.com/__down?bytes=262144"
TRANSFER_BYTES = 262144
MAX_CHANNELS = 64


@dataclass
class SelectionResult:
    profile: Profile
    rows: list[dict]
    revision: str
    created_at: float
    require_udp: bool
    duration: int
    source_digest: str = ""
    operation_id: str = ""
    server_state: str = ""
    previous_profile: Profile | None = None
    local_applied: bool = False
    restored_config: dict | None = None


def config_digest(config):
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def command_url(url):
    parsed = urlsplit(validate_url(url))
    path = parsed.path.rstrip("/")
    parts = path.split("/")
    if not (
        (len(parts) == 3 and parts[1] == "s")
        or (len(parts) == 5 and parts[1:4] == ["api", "mihomo", "subscriptions"])
    ):
        raise ClientError("Команда доступна для ссылки подписки PRO вида /s/… .")
    if not parts[-1]:
        raise ClientError("В ссылке подписки отсутствует токен.")
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/commands/select-channels", "", ""))


def command_headers(device_id, core_version=None):
    name, os_version = windows_identity()
    return {
        "Accept": "application/json",
        "User-Agent": f"Nodera/{__version__} (Windows; Mihomo)",
        "X-Device-ID": device_id,
        "X-Device-Name": name,
        "X-Device-OS": "windows",
        "X-OS-Version": os_version,
        "X-Client-Name": "Nodera",
        "X-Client-Version": __version__,
        "X-Core-Name": "mihomo",
        "X-Core-Version": core_version or core_manifest()["version"].removeprefix("v"),
    }


def request_candidates(
    profile, device_id, require_udp=False, *, proxy=None, transport=None, core_version=None
):
    headers = command_headers(device_id, core_version)
    url = command_url(profile.url)
    started = time.monotonic()
    try:
        with httpx.Client(
            proxy=proxy,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(45, connect=10),
        ) as client:
            with client.stream(
                "POST", url, headers=headers, json={"require_udp": require_udp}
            ) as response:
                messages = {
                    403: "Команда недоступна: проверьте регистрацию HWID, срок и лимит подписки.",
                    404: "Сервер не поддерживает подбор каналов или ссылка подписки устарела.",
                    409: "Сервер пока не может подготовить каналы. Проверьте профиль и Relay.",
                    429: "Сервер занят проверкой. Повторите подбор позже.",
                }
                if response.status_code in messages:
                    raise ClientError(messages[response.status_code])
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if time.monotonic() - started > 45:
                        raise ClientError("Истекло время получения плана проверки каналов.")
                    if len(body) > MAX_CONFIG_BYTES * 2:
                        raise ClientError("Ответ подбора каналов превышает допустимый размер.")
                data = json.loads(body)
    except (httpx.HTTPError, ValueError) as exc:
        raise ClientError(
            "Не удалось получить каналы PRO. Проверьте сеть и версию сервера."
        ) from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or data.get("command") != "select-channels"
        or not isinstance(data.get("config"), str)
        or not isinstance(data.get("revision"), str)
        or type(data.get("expires_at")) is not int
        or data["expires_at"] <= time.time()
        or data.get("require_udp") is not require_udp
    ):
        raise ClientError("Сервер вернул несовместимый или просроченный план проверки.")
    config = parse_config(data["config"].encode())
    if config.get("proxy-providers"):
        raise ClientError(
            "Для подбора нужен профиль с явным списком каналов без внешних источников подключений."
        )
    if len(visible_proxies(config)) > MAX_CHANNELS:
        raise ClientError("В профиле больше 64 каналов. Уменьшите профиль для подбора.")
    return config, data["revision"]


class CommandUncertain(ClientError):
    """A mutation may have committed; read its status instead of repeating it."""


def server_json(profile, device_id, suffix, *, payload=None, proxy=None, transport=None):
    method = "GET" if payload is None else "POST"
    try:
        with httpx.Client(
            proxy=proxy, transport=transport, trust_env=False, follow_redirects=False, timeout=40
        ) as client:
            with client.stream(
                method,
                command_url(profile.url) + suffix,
                headers=command_headers(device_id),
                json=payload,
            ) as response:
                if 400 <= response.status_code < 500:
                    raise ClientError(
                        "Команда отклонена сервером. Профиль мог измениться; проверьте состояние или повторите подбор."
                    )
                response.raise_for_status()
                content = bytearray()
                started = time.monotonic()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_CONFIG_BYTES * 2 or time.monotonic() - started > 40:
                        raise ValueError
                data = json.loads(content)
                if not isinstance(data, dict) or data.get("version") != 1:
                    raise ValueError
                return data
    except (httpx.HTTPError, ValueError) as exc:
        raise CommandUncertain(
            "Ответ сервера не подтверждён. Требуется проверка состояния команды."
        ) from exc


def read_server_selection(result, device_id, *, proxy=None, transport=None):
    data = server_json(
        result.profile, device_id, "/" + result.operation_id, proxy=proxy, transport=transport
    )
    return update_server_selection(result, data)


def discover_server_selection(profile, device_id, *, proxy=None, transport=None):
    data = server_json(profile, device_id, "", proxy=proxy, transport=transport)
    identity = data.get("operation_id", "")
    if (
        not isinstance(identity, str)
        or len(identity) != 32
        or any(c not in "0123456789abcdef" for c in identity)
    ):
        raise ClientError("Сохранённая версия подписки для отката не найдена.")
    # Discovery is for rollback only; it never labels an untested profile ready.
    return SelectionResult(
        profile,
        [],
        data.get("revision", ""),
        time.time(),
        False,
        data.get("duration", 60),
        operation_id=identity,
        server_state=data.get("state", "unknown"),
        previous_profile=profile,
        local_applied=True,
    )


def update_server_selection(result, data):
    if data.get("operation_id") != result.operation_id or data.get("state") not in {
        "pending",
        "accepted",
        "rejected",
        "rolled_back",
        "expired",
        "stale",
    }:
        raise CommandUncertain("Сервер вернул несовместимое состояние команды.")
    try:
        text = data.get("config")
        if not isinstance(text, str):
            raise ClientError("Отсутствует конфигурация")
        config = parse_config(text.encode())
    except ClientError as exc:
        raise CommandUncertain("Состояние команды получено без корректного профиля.") from exc
    result.server_state = data["state"]
    if result.server_state in {"pending", "accepted"}:
        if config_digest(config) != config_digest(result.profile.config):
            raise CommandUncertain(
                "Подготовленная сервером конфигурация отличается от проверенной."
            )
        result.profile = replace(result.profile, config=config)
    else:
        result.restored_config = config
    return result


def change_server_selection(result, device_id, action=None, *, proxy=None, transport=None):
    if not result.operation_id:
        result.operation_id = uuid4().hex
    if action is None:
        payload = {
            "operation_id": result.operation_id,
            "revision": result.revision,
            "source_digest": result.source_digest,
            "duration": result.duration,
            "require_udp": result.require_udp,
            "reports": [
                {
                    key: ("failed" if key == "state" and row[key] == "skipped" else row[key])
                    for key in ("name", "state", "seconds", "transfers", "bytes", "udp")
                    if key in row
                }
                for row in result.rows
            ],
        }
        suffix = "/prepare"
    else:
        payload = {"action": action}
        suffix = "/" + result.operation_id + "/decision"
    try:
        data = server_json(
            result.profile, device_id, suffix, payload=payload, proxy=proxy, transport=transport
        )
        return update_server_selection(result, data)
    except CommandUncertain:
        try:
            return read_server_selection(result, device_id, proxy=proxy, transport=transport)
        except ClientError:
            result.server_state = "unknown"
            return result


def receive_exact(sock, count):
    value = bytearray()
    while len(value) < count:
        chunk = sock.recv(count - len(value))
        if not chunk:
            raise OSError("SOCKS connection closed")
        value.extend(chunk)
    return bytes(value)


def udp_probe(port, *, destination=("1.1.1.1", 53)):
    """A real DNS datagram through this channel's SOCKS5 UDP association."""
    query_id = secrets.token_bytes(2)
    question = b"\x07example\x03com\x00\x00\x01\x00\x01"
    query = query_id + struct.pack("!5H", 0x0100, 1, 0, 0, 0) + question
    destination_header = (
        b"\x00\x00\x00\x01" + socket.inet_aton(destination[0]) + struct.pack("!H", destination[1])
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as tcp:
        tcp.settimeout(5)
        tcp.sendall(b"\x05\x01\x00")
        if receive_exact(tcp, 2) != b"\x05\x00":
            raise OSError("SOCKS authentication refused")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(("127.0.0.1", 0))
            udp.settimeout(5)
            tcp.sendall(
                b"\x05\x03\x00\x01\x7f\x00\x00\x01" + struct.pack("!H", udp.getsockname()[1])
            )
            head = receive_exact(tcp, 4)
            if head[:3] != b"\x05\x00\x00" or head[3] != 1:
                raise OSError("SOCKS UDP association unavailable")
            host = socket.inet_ntoa(receive_exact(tcp, 4))
            relay_port = struct.unpack("!H", receive_exact(tcp, 2))[0]
            if host == "0.0.0.0":
                host = "127.0.0.1"
            if host != "127.0.0.1" or not relay_port:
                raise OSError("Unexpected SOCKS UDP relay")
            udp.connect((host, relay_port))
            udp.send(destination_header + query)
            answer = udp.recv(65535)
    if len(answer) < 22 or answer[:10] != destination_header:
        raise OSError("Unexpected UDP source")
    dns = answer[10:]
    flags, questions, answers = struct.unpack("!3H", dns[2:8])
    if (
        dns[:2] != query_id
        or flags & 0x800F != 0x8000
        or flags & 0x0200
        or questions != 1
        or answers < 1
        or dns[12 : 12 + len(question)] != question
    ):
        raise OSError("Invalid UDP DNS answer")


def transfer_probe(port):
    """Fresh HTTPS connection and complete bounded payload, never a latency-only test."""
    started = time.monotonic()
    try:
        with httpx.Client(
            proxy=f"http://127.0.0.1:{port}",
            trust_env=False,
            follow_redirects=False,
            timeout=10,
            headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache"},
        ) as client:
            with client.stream("GET", TRANSFER_URL) as response:
                if response.status_code != 200:
                    raise ClientError("Передача данных вернула неожиданный ответ.")
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if time.monotonic() - started > 20:
                        raise ClientError("Проверочная передача превысила допустимое время.")
                    if size > TRANSFER_BYTES:
                        raise ClientError("Размер проверочного ответа не совпал.")
                if size != TRANSFER_BYTES:
                    raise ClientError("Проверочная передача оборвалась.")
                return size
    except httpx.HTTPError as exc:
        raise ClientError("Передача данных через канал не прошла.") from exc


def check_cancel(cancel):
    if cancel.is_set():
        raise ClientError("Подбор каналов отменён. Текущая подписка сохранена.")


def sustained_probe(core, duration, require_udp, cancel, progress):
    start = time.monotonic()
    count = size = 0
    udp_ok = True
    while True:
        check_cancel(cancel)
        core.check_connectivity()
        size += transfer_probe(core.mixed_port)
        try:
            udp_probe(core.mixed_port)
        except OSError:
            udp_ok = False
            if require_udp:
                raise ClientError("Канал не прошёл проверку передачи UDP.") from None
        count += 1
        elapsed = time.monotonic() - start
        progress(f"{int(elapsed)} / {duration} с · передач: {count} · {size // 1024} КБ")
        if elapsed >= duration and count >= 3:
            break
        if cancel.wait(min(5, max(0, duration - elapsed))):
            check_cancel(cancel)
    return {
        "state": "passed",
        "seconds": round(time.monotonic() - start, 1),
        "transfers": count,
        "bytes": size,
        "udp": udp_ok,
    }


def selected_config(config, names):
    """Keep accepted routes/dependencies and preserve the server routing policy."""
    nodes = {n["name"]: n for n in config["proxies"]}
    keep = set(names)
    pending = list(names)
    while pending:
        dependency = nodes[pending.pop()].get("dialer-proxy")
        if dependency:
            if dependency not in nodes:
                raise ClientError("Канал использует неподдерживаемую группу транспорта.")
            if dependency not in keep:
                keep.add(dependency)
                pending.append(dependency)
    result = copy.deepcopy(config)
    result["proxies"] = [copy.deepcopy(n) for n in config["proxies"] if n["name"] in keep]
    # Preserve server routing/groups/providers. Remove failed explicit nodes from
    # every group; empty groups are repaired to accepted channels, never DIRECT.
    failed = set(nodes) - keep
    for group in result.get("proxy-groups", []):
        group["proxies"] = [n for n in group.get("proxies", []) if n not in failed]
        if not group["proxies"]:
            group["proxies"] = list(names)

    # Rules may name a concrete node rather than a group. Preserve the match
    # expression, replacing only a failed target with an accepted route.
    def repair(rule):
        parts = rule.split(",")
        target = -2 if parts[-1].lower() == "no-resolve" else -1
        if len(parts) >= 2 and parts[target] in failed:
            parts[target] = names[0]
        return ",".join(parts)

    result["rules"] = [repair(rule) for rule in result.get("rules", [])]
    for key, rules in result.get("sub-rules", {}).items():
        result["sub-rules"][key] = [repair(rule) for rule in rules]
    return result


def select_channels(
    profile,
    device_id,
    root,
    *,
    duration=60,
    require_udp=False,
    policy=None,
    proxy=None,
    cancel=None,
    progress=lambda _: None,
):
    if duration not in {30, 60, 120}:
        raise ClientError("Выберите проверку на 30, 60 или 120 секунд.")
    policy = (policy or ChannelPolicy()).validate()
    cancel = cancel or Event()
    check_cancel(cancel)
    progress("Получение каналов от сервера…")
    manager = CoreUpdates(Path(root).parent)
    config, revision = request_candidates(
        profile, device_id, require_udp, proxy=proxy, core_version=manager.version()
    )
    check_cancel(cancel)
    try:
        _, _, interface = physical_interface(with_name=True)
    except ClientError:
        raise ClientError(
            "Не найден физический интерфейс для независимой проверки каналов."
        ) from None
    candidates = visible_proxies(config)
    if not any(policy.matches(node) for node in candidates):
        raise ClientError(
            "В подписке нет каналов с выбранными протоколами, транспортом и TLS. Измените параметры."
        )
    # No rules/providers/TUN/system proxy in the disposable checker. GLOBAL is
    # pinned to each explicit node so another healthy route cannot mask failure.
    probe_config = {
        "proxies": copy.deepcopy(config["proxies"]),
        "mode": "global",
        "dns": {
            "enable": True,
            "enhanced-mode": "redir-host",
            "default-nameserver": ["1.1.1.1", "8.8.8.8"],
            "nameserver": ["https://1.1.1.1/dns-query"],
            "proxy-server-nameserver": ["https://1.1.1.1/dns-query"],
        },
    }
    if "global-client-fingerprint" in config:
        probe_config["global-client-fingerprint"] = config["global-client-fingerprint"]
    rows = []
    Path(root).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="channel-check-", dir=root) as temp:
        core = Core(
            Path(temp) / "runtime",
            core_resolver=manager.resolve,
            outbound_interface=interface,
        )
        try:
            core.start(
                replace(
                    profile, config=probe_config, selections={}, forced_server="", local_rules=[]
                ),
                Settings(
                    tun=False,
                    system_proxy=False,
                    ipv6_mode="off",
                    save_logs=False,
                    save_connection_logs=False,
                ),
            )
            accepted_count = 0
            for index, node in enumerate(candidates):
                check_cancel(cancel)
                # The server requires one report per granted channel. Exclusions
                # are non-passing reports on the wire, never fabricated probes.
                if not policy.matches(node) or (
                    policy.max_channels and accepted_count >= policy.max_channels
                ):
                    rows.append({"name": node["name"], "state": "skipped"})
                    continue
                title = f"Канал {index + 1} из {len(candidates)} · {node['name']}"
                progress(title)
                try:
                    core.force_server(node["name"])
                    row = sustained_probe(
                        core,
                        duration,
                        require_udp,
                        cancel,
                        lambda value: progress(title + "\n" + value),
                    )
                except (ClientError, OSError):
                    check_cancel(cancel)
                    row = {"state": "failed", "reason": "Проверка передачи не пройдена"}
                rows.append({"name": node["name"], **row})
                accepted_count += row["state"] == "passed"
            accepted = [r["name"] for r in rows if r["state"] == "passed"]
            if not accepted:
                raise ClientError(
                    "Ни один канал не прошёл длительную проверку. Текущая подписка сохранена."
                )
            check_cancel(cancel)
            selected = selected_config(config, accepted)
            result_profile = replace(
                profile,
                channel_capabilities=channel_capabilities(config),
                config=selected,
                forced_server="",
                selections={},
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            progress("Проверка итогового профиля в отдельном ядре…")
            # Validate full rules/groups/providers only after all isolated channel
            # tests. This remains the disposable core, never the user's session.
            core.apply_profile(result_profile)
            core.check_connectivity()
            transfer_probe(core.mixed_port)
            if require_udp:
                udp_probe(core.mixed_port)
        finally:
            core.stop("channel selection finished")
    check_cancel(cancel)
    return SelectionResult(
        result_profile, rows, revision, time.time(), require_udp, duration, config_digest(config)
    )


def apply_selection(result, previous, library, store, core, connected):
    """Explicit user action: apply + verify before persistence; rollback on failure."""
    if result.profile.id != previous.id or time.time() - result.created_at > 1800:
        raise ClientError("Результат проверки устарел. Выполните подбор заново.")
    updated = library.upsert(result.profile, activate=False)
    if previous.config != result.profile.config:
        updated = updated.remember(previous)
    groups = None
    try:
        if connected:
            groups = core.apply_profile(result.profile)
            core.check_connectivity()
            transfer_probe(core.mixed_port)
            if result.require_udp:
                udp_probe(core.mixed_port)
        store.save_library(updated)
    except (ClientError, OSError):
        if connected:
            try:
                core.apply_profile(previous)
            except (ClientError, OSError):
                raise ClientError(
                    "Не удалось применить профиль и восстановить сеанс. Сохранённая подписка не изменена; переподключитесь."
                ) from None
        raise ClientError("Профиль не применён. Прежняя подписка и настройки сохранены.") from None
    return updated, groups
