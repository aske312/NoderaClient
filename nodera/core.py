"""Owns one Mihomo process and its private, short-lived configuration."""

import copy
import ctypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import replace
from functools import wraps
from pathlib import Path

import httpx
import yaml

from nodera.bundled_core import resolve_core
from nodera.connection_journal import ConnectionJournal
from nodera.connection_trace import ConnectionTrace
from nodera.controller import Controller
from nodera.geodata import prepare_session_geodata
from nodera.journal import Journal, profile_secrets, redact
from nodera.models import ClientError, Profile, Settings
from nodera.ports import choose_ports, listener_owners
from nodera.profile_view import effective_tun
from nodera.provider_cache import RuleCache
from nodera.route_health import RouteHealth, probe_name, quarantine_config
from nodera.storage import atomic_write
from nodera.system_proxy import SystemProxy
from nodera.tun_address import choose_tun_range, ipv4_assignments

CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
CONNECTIVITY_URL = "https://www.gstatic.com/generate_204"
REMOTE_KEYS = {
    "proxies",
    "proxy-groups",
    "proxy-providers",
    "rule-providers",
    "rules",
    "sub-rules",
    "dns",
    "hosts",
    "sniffer",
    "ipv6",
    "tcp-concurrent",
    "unified-delay",
    "find-process-mode",
    "geodata-mode",
    "geodata-loader",
    "geox-url",
    "geo-auto-update",
    "geo-update-interval",
    "global-client-fingerprint",
    "keep-alive-interval",
    "keep-alive-idle",
    "disable-keep-alive",
    "global-ua",
    "etag-support",
}


class PortConflict(Exception):
    """Transient bind race; retry internally instead of asking for manual setup."""


def ready_version(client, require_tun=False):
    response = client.get("/version")
    response.raise_for_status()
    version = response.json().get("version", "Mihomo")
    if require_tun:
        actual = client.get("/configs")
        actual.raise_for_status()
        if actual.json().get("tun", {}).get("enable") is not True:
            return None  # The controller can start before the Windows adapter.
    return str(version)


def runtime_config(
    config: dict,
    settings: Settings,
    controller_port: int,
    secret: str,
    local_rules=None,
    fake_ip_range=None,
) -> dict:
    # Server supplies routing; Nodera owns all local listeners and control surfaces.
    result = copy.deepcopy({key: value for key, value in config.items() if key in REMOTE_KEYS})
    for node in result.get("proxies", []):
        node.pop("x-nodera-route", None)
    for group in result.get("proxy-groups", []):
        if isinstance(group, dict):
            group.pop("external-controller", None)
            if group.get("type") in {"fallback", "url-test", "load-balance"}:
                group.setdefault("url", CONNECTIVITY_URL)
                group.setdefault("interval", 60)
                group.setdefault("lazy", False)
                group.setdefault("timeout", 5000)
                group.setdefault("max-failed-times", 2)
    for section in ("proxy-providers", "rule-providers"):
        for index, provider in enumerate(result.get(section, {}).values()):
            provider["path"] = f"./providers/{section}-{index}.cache"
    dns = result.get("dns")
    if isinstance(dns, dict):
        dns.pop("listen", None)
    original_tun = config.get("tun", {})
    tun = {
        key: copy.deepcopy(value)
        for key, value in original_tun.items()
        if key in {"stack", "mtu", "dns-hijack", "strict-route", "udp-timeout"}
    }
    enabled = effective_tun(config, settings)
    tun.update(
        {
            "enable": enabled,
            "device": "Nodera",
            "auto-route": enabled,
            "auto-detect-interface": True,
        }
    )
    if enabled:
        tun.setdefault("stack", "mixed")
        tun.setdefault("strict-route", True)
        if not tun.get("dns-hijack"):
            tun["dns-hijack"] = ["any:53"]
        # DNS hijacking needs a running resolver even if the subscription disables DNS.
        if not isinstance(dns, dict):
            dns = {}
            result["dns"] = dns
        dns["enable"] = True
        dns["fake-ip-range"] = fake_ip_range or "198.19.0.1/16"
        dns.setdefault("enhanced-mode", "fake-ip")
        if not dns.get("nameserver"):
            dns["nameserver"] = ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"]
        if not dns.get("default-nameserver"):
            dns["default-nameserver"] = ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"]
    if settings.tun_stack != "profile":
        tun["stack"] = settings.tun_stack
    if settings.tun_mtu:
        tun["mtu"] = settings.tun_mtu
    if settings.ipv6_mode != "profile":
        result["ipv6"] = settings.ipv6_mode == "on"
        if isinstance(dns, dict):
            dns["ipv6"] = result["ipv6"]
    if local_rules:
        result["rules"] = list(local_rules) + list(result.get("rules", []))
    result.update(
        {
            "mixed-port": settings.mixed_port,
            "allow-lan": False,
            "bind-address": "127.0.0.1",
            "external-controller": f"127.0.0.1:{controller_port}",
            "secret": secret,
            "mode": config.get("mode")
            if config.get("mode") in {"rule", "global", "direct"}
            else "rule",
            "log-level": settings.log_level,
            "find-process-mode": "always",
            "tun": tun,
        }
    )
    return result


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def is_admin() -> bool:
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def system_tool(name):
    directory = ctypes.create_unicode_buffer(32768)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    length = kernel.GetSystemDirectoryW(directory, len(directory))
    if not 0 < length < len(directory):
        raise ClientError("Windows не предоставила путь к системным компонентам.")
    return str(Path(directory.value) / name)


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        identity = subprocess.run(
            [system_tool("whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            check=True,
            creationflags=CREATE_FLAGS,
        )
        match = re.search(rb"S-1-\d+(?:-\d+)+", identity.stdout)
        if not match:
            raise ClientError("Windows не предоставила идентификатор пользователя.")
        sid = match[0].decode("ascii")
        subprocess.run(
            [
                system_tool("icacls.exe"),
                str(path.resolve()),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
            ],
            capture_output=True,
            check=True,
            creationflags=CREATE_FLAGS,
        )
    else:
        path.chmod(0o700)


class ProcessJob:
    """Windows closes the core if the desktop process crashes or is terminated."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")
            ]

        class Limits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", IO),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise ClientError("Не удалось создать изолированную группу процессов Windows.")

    def assign(self, process: subprocess.Popen) -> None:
        if self.handle and not self.kernel.AssignProcessToJobObject(
            self.handle, int(process._handle)
        ):
            raise ClientError("Windows не разрешила безопасный запуск ядра.")

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._operation_lock:
            return method(self, *args, **kwargs)

    return call


class Core:
    def __init__(self, root: Path, core_resolver=None):
        self.root = root
        self._operation_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self.route_health = RouteHealth()
        self.frozen_routes = {}
        self._log_lock = threading.Lock()
        self._logs = deque(maxlen=2000)
        self._reader = None
        self._resolve_core = core_resolver or resolve_core
        self.process: subprocess.Popen | None = None
        self.runtime: Path | None = None
        self.job: ProcessJob | None = None
        self.port = 0
        self.mixed_port = 0
        self.secret = ""
        self.group_names: list[str] = []
        self.node_names: list[str] = []
        self.started_at = 0.0
        self._traffic_sample = None
        self.system_proxy = SystemProxy(root.parent)
        self.trace = Journal(root.parent, filename="network.jsonl", max_bytes=8 * 1024 * 1024)
        self._session_secrets = ()
        self._start_time = 0.0
        self._startup_failure = None
        self._dropped_logs = 0
        self._connections = ConnectionTrace()
        self.connection_log = ConnectionJournal(root.parent)
        self._rule_cache = None

    def _record(self, message, level="info", source="Core"):
        entry = self.trace.add(message, level, source, self._session_secrets)
        self._queue_log(level, f"[{source}] {entry['message']}")

    def _queue_log(self, level, message):
        with self._log_lock:
            if len(self._logs) == self._logs.maxlen:
                self._dropped_logs += 1
            self._logs.append((level, message))

    @property
    def running(self) -> bool:
        process = self.process
        return process is not None and process.poll() is None

    @serialized
    def start(self, profile: Profile, settings: Settings) -> str:
        if self.running:
            raise ClientError("Ядро уже запущено.")
        self.stop("prepare new session")
        self._stop_requested.clear()
        self.trace.configure(settings.log_limit, settings.save_logs)
        self.connection_log.configure(
            settings.save_connection_logs,
            settings.connection_log_mb,
            settings.connection_log_interval,
        )
        self._session_secrets = profile_secrets(profile)
        self._start_time = time.monotonic()
        self._record(
            f"event=start_requested client_pid={os.getpid()} mode={'TUN' if settings.tun else 'Proxy'} admin={is_admin() if settings.tun else 'not-required'} local_rules={len(profile.local_rules)}"
        )
        binary = self._resolve_core()
        if effective_tun(profile.config, settings) and not is_admin():
            raise ClientError(
                "Для TUN нужны права администратора. Нажмите подключение в Nodera и подтвердите запрос Windows."
            )
        for attempt in range(3):
            self.mixed_port, self.port = choose_ports(settings.mixed_port if attempt == 0 else 0)
            try:
                return self._start(profile, replace(settings, mixed_port=self.mixed_port), binary)
            except PortConflict:
                self.stop("port conflict; retry")
        raise ClientError(
            "Не удалось автоматически занять локальные порты. Проверьте сетевые ограничения Windows и повторите подключение."
        )

    def _start(self, profile, settings, binary):
        self._session_settings = copy.deepcopy(settings)
        self.secret = secrets.token_urlsafe(32)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = Path(tempfile.mkdtemp(prefix="session-", dir=self.root))
        try:
            self._startup_failure = None
            secure_directory(self.runtime)
            prepare_session_geodata(self.runtime)
            config_path = self.runtime / "config.yaml"
            address_range = choose_tun_range() if settings.tun else None
            config = runtime_config(
                profile.config, settings, self.port, self.secret, profile.local_rules, address_range
            )
            self._rule_cache = RuleCache(
                self.root.parent, profile, config, self.runtime, self._record
            )
            self._rule_cache.restore()
            self._session_secrets = (*profile_secrets(profile), self.secret)
            self._record(
                f"event=session_prepared session={self.runtime.name} proxy_port={settings.mixed_port} api_port={self.port} tun_range={address_range or '-'}"
            )
            self.group_names = [
                group["name"]
                for group in config.get("proxy-groups", [])
                if isinstance(group, dict)
                and isinstance(group.get("name"), str)
                and not group.get("hidden", False)
            ]
            self.node_names = [node["name"] for node in config.get("proxies", [])]
            self._policy_mode = config["mode"]
            self._base_config = copy.deepcopy(config)
            self.route_health = RouteHealth()
            self.forced_server = ""
            if config["mode"] == "global" and "GLOBAL" not in self.group_names:
                self.group_names.insert(0, "GLOBAL")
            atomic_write(
                config_path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode()
            )
            command = [
                str(binary.resolve()),
                "-d",
                str(self.runtime.resolve()),
                "-f",
                str(config_path.resolve()),
            ]
            validation_started = time.monotonic()
            self._record("event=validation_started timeout_s=25")
            validation = subprocess.run(
                [*command, "-t"], capture_output=True, timeout=25, creationflags=CREATE_FLAGS
            )
            self._record(
                f"event=validation_finished exit_code={validation.returncode} elapsed_ms={int((time.monotonic() - validation_started) * 1000)}"
            )
            if validation.returncode:
                output = (validation.stdout or b"") + (validation.stderr or b"")
                for line in output.decode("utf-8", errors="replace").splitlines()[-100:]:
                    clean = redact(line, self._session_secrets)
                    self.trace.add(clean, "error", "Validation", self._session_secrets)
                    self._queue_log("error", clean)
                text = output.decode("utf-8", errors="replace").lower()
                if any(word in text for word in ("mmdb", "geosite", "geoip", "geodata")):
                    raise ClientError(
                        "Не удалось загрузить геоданные правил. Подробности — в журнале Mihomo."
                    )
                raise ClientError(
                    "Ядро отклонило конфигурацию подписки. Подробности — в журнале Mihomo."
                )
            self.job = ProcessJob()
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_FLAGS,
            )
            self.job.assign(self.process)
            self._record(f"event=process_started pid={self.process.pid}")
            self._reader = threading.Thread(
                target=self._read_output,
                args=(self.process, (*profile_secrets(profile), self.secret)),
                daemon=True,
            )
            self._reader.start()
            tun_pending = False
            last_stage = None
            last_progress = 0.0
            with self._client() as client:
                deadline = time.monotonic() + (30 if config["tun"]["enable"] else 15)
                while (
                    time.monotonic() < deadline
                    and self.running
                    and not self._stop_requested.is_set()
                ):
                    now = time.monotonic()
                    if now - last_progress >= 5:
                        self._record(
                            f"event=start_progress pid={self.process.pid} elapsed_ms={int((now - self._start_time) * 1000)} remaining_ms={max(0, int((deadline - now) * 1000))} tun_pending={tun_pending}"
                        )
                        last_progress = now
                    owners = listener_owners(self.mixed_port, self.port)
                    stage = (
                        "listeners"
                        if not all(self.process.pid in pids for pids in owners.values())
                        else "controller_or_tun"
                    )
                    if stage != last_stage:
                        self._record(
                            f"event=waiting stage={stage} elapsed_ms={int((time.monotonic() - self._start_time) * 1000)}"
                        )
                        last_stage = stage
                    if any(pids and self.process.pid not in pids for pids in owners.values()):
                        raise PortConflict()
                    if not all(self.process.pid in pids for pids in owners.values()):
                        time.sleep(0.1)
                        continue
                    try:
                        version = ready_version(client, config["tun"]["enable"])
                        if version is None:
                            if self._startup_failure:
                                raise ClientError(self._startup_failure)
                            tun_pending = True
                            time.sleep(0.1)
                            continue
                        if settings.tun:
                            wanted = address_range.split("/")[0]
                            if wanted not in {str(item.ip) for item in ipv4_assignments()}:
                                tun_pending = True
                                time.sleep(0.1)
                                continue
                        tun_pending = False
                        # A live controller alone does not guarantee that the proxy bound.
                        with socket.create_connection(
                            ("127.0.0.1", settings.mixed_port), timeout=1
                        ):
                            self.started_at = time.monotonic()
                            self._traffic_sample = None
                            self.restore_selections(profile.selections)
                            if profile.forced_server:
                                self.force_server(profile.forced_server)
                            if settings.system_proxy and not effective_tun(
                                profile.config, settings
                            ):
                                self.system_proxy.enable(settings.mixed_port)
                            self._record(
                                f"event=session_ready pid={self.process.pid} mode={'TUN' if settings.tun else 'Proxy'} adapter={'Nodera' if settings.tun else '-'} elapsed_ms={int((time.monotonic() - self._start_time) * 1000)}"
                            )
                            return str(version)
                    except (httpx.HTTPError, ValueError, OSError):
                        time.sleep(0.15)
            if self._stop_requested.is_set():
                raise ClientError("Подключение отменено при завершении приложения.")
            if tun_pending:
                raise ClientError(
                    "Windows не завершила запуск TUN за 30 секунд. Подробности — в журнале ядра."
                )
            owners = listener_owners(self.mixed_port, self.port)
            if any(pids and self.process.pid not in pids for pids in owners.values()):
                raise PortConflict()
            if (
                self.running
                and not owners[self.mixed_port]
                and self.process.pid in owners[self.port]
            ):
                raise PortConflict()
            raise ClientError(
                "Ядро не запустилось. Проверьте профиль, права TUN и локальные порты."
            )
        except Exception as exc:
            self._record(f"event=start_failed type={type(exc).__name__} detail={exc}", "error")
            self.stop(f"startup failed: {type(exc).__name__}")
            raise

    def check_connectivity(self):
        port = self.mixed_port
        if not self.running or not port:
            raise ClientError("Сначала запустите подключение.")
        try:
            started = time.monotonic()
            self._record(f"event=https_probe_started proxy_port={port} read_timeout_s=6")
            with httpx.Client(
                proxy=f"http://127.0.0.1:{port}",
                trust_env=False,
                timeout=6,
            ) as client:
                response = client.get(CONNECTIVITY_URL)
                self._record(
                    f"event=https_probe_response status={response.status_code} elapsed_ms={int((time.monotonic() - started) * 1000)}"
                )
                if response.status_code != 204:
                    raise ClientError("Проверочный HTTPS-запрос вернул неожиданный ответ.")
        except httpx.HTTPError as exc:
            self._record(
                f"event=https_probe_failed type={type(exc).__name__} detail={exc} elapsed_ms={int((time.monotonic() - started) * 1000)} core_running={self.running}",
                "warning",
            )
            raise ClientError(
                "HTTPS-запрос через Nodera не прошёл. Проверьте DNS и журнал ядра."
            ) from exc

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}",
            timeout=1,
            headers={"Authorization": f"Bearer {self.secret}"},
            trust_env=False,
        )

    @serialized
    def status(self) -> dict:
        if not self.running:
            self.stop("process exited before status poll")
            raise ClientError("Ядро остановилось. Подключитесь повторно.")
        try:
            with self._client() as client:
                response = client.get("/connections")
                response.raise_for_status()
                data = response.json()
                if self._rule_cache:
                    self._rule_cache.capture()
                now = time.monotonic()
                for event in self._connections.update(data.get("connections", [])):
                    self._record(event, source="Connections")
                self.connection_log.update(
                    data.get("connections", []), now=now, secrets=self._session_secrets
                )
                download = int(data.get("downloadTotal", 0))
                upload = int(data.get("uploadTotal", 0))
                down_rate = up_rate = 0
                if self._traffic_sample:
                    previous_time, previous_down, previous_up = self._traffic_sample
                    elapsed = max(now - previous_time, 0.001)
                    down_rate = max(0, int((download - previous_down) / elapsed))
                    up_rate = max(0, int((upload - previous_up) / elapsed))
                self._traffic_sample = now, download, upload
                return {
                    "download": download,
                    "upload": upload,
                    "download_rate": down_rate,
                    "upload_rate": up_rate,
                    "uptime": int(now - self.started_at),
                    "connections": len(data.get("connections") or []),
                    "flows": data.get("connections") or [],
                }
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ClientError("Статистика ядра временно недоступна.") from exc

    @serialized
    def groups(self) -> list[dict]:
        if not self.running:
            raise ClientError("Сначала запустите подключение.")
        with Controller(self.port, self.secret) as controller:
            return controller.groups(self.group_names, self.node_names)

    @serialized
    def select(self, group: str, member: str) -> list[dict]:
        if not self.running:
            raise ClientError("Сначала запустите подключение.")
        if group not in self.group_names:
            raise ClientError("Эта группа не входит в текущую подписку.")
        if member in self.route_health.frozen:
            raise ClientError("Маршрут временно заморожен до успешной повторной проверки.")
        with Controller(self.port, self.secret) as controller:
            controller.select(group, member)
        return self.groups()

    @serialized
    def force_server(self, member):
        if not self.running:
            raise ClientError("Сначала запустите подключение.")
        if member not in self.node_names:
            raise ClientError("Выберите конкретный маршрут подписки.")
        if member in self.route_health.frozen:
            raise ClientError("Маршрут временно заморожен до успешной повторной проверки.")
        with Controller(self.port, self.secret) as controller:
            controller.force_server(member)
        self.forced_server = member
        if "GLOBAL" not in self.group_names:
            self.group_names.insert(0, "GLOBAL")
        return self.groups()

    @serialized
    def restore_policy(self):
        if not self.running:
            raise ClientError("Сначала запустите подключение.")
        with Controller(self.port, self.secret) as controller:
            controller.request("PATCH", "/configs", json={"mode": self._policy_mode})
        self.forced_server = ""
        if self._policy_mode != "global":
            self.group_names = [name for name in self.group_names if name != "GLOBAL"]
        return self.groups()

    @serialized
    def close_connections(self, identities):
        if not self.running:
            raise ClientError("Сначала запустите подключение.")
        closed, failed = [], 0
        with Controller(self.port, self.secret) as controller:
            for identity in dict.fromkeys(identities):
                try:
                    controller.close_connection(identity)
                    closed.append(identity)
                except ClientError:
                    failed += 1
        return closed, failed

    @serialized
    def recheck_routes(self):
        if not self.running or getattr(self, "forced_server", ""):
            return []
        with Controller(self.port, self.secret) as controller:
            return controller.recheck_routes(self.group_names, CONNECTIVITY_URL)

    @serialized
    def delay(self, member: str) -> int:
        if not self.running:
            raise ClientError("Сначала запустите подключение.")
        with Controller(self.port, self.secret) as controller:
            return controller.delay(
                probe_name(member) if member in self.route_health.frozen else member
            )

    @serialized
    def probe_aliases(self):
        return {name: probe_name(name) for name in self.route_health.frozen}

    @serialized
    def update_route_health(self, values):
        if not self.running:
            return
        previous = copy.deepcopy(self.route_health)
        self.route_health.update(values, time.monotonic())
        if set(previous.frozen) == set(self.route_health.frozen):
            return
        try:
            self._reload_quarantine(previous)
        except Exception:
            self.route_health = previous
            raise
        self.frozen_routes = dict(self.route_health.frozen)
        self._record(f"event=route_quarantine_changed count={len(self.route_health.frozen)}")

    def _reload_quarantine(self, previous):
        before = quarantine_config(self._base_config, previous.frozen)
        after = quarantine_config(self._base_config, self.route_health.frozen)
        with Controller(self.port, self.secret) as controller:
            current = controller.request("GET", "/configs")
            for config in (before, after):
                config["mode"] = current.get("mode", self._policy_mode)
                config["log-level"] = current.get("log-level", config["log-level"])
            choices = {
                name: item.get("now")
                for name, item in controller.proxies().items()
                if item.get("type") == "Selector" and isinstance(item.get("now"), str)
            }
            try:
                controller.request(
                    "PUT",
                    "/configs",
                    params={"force": "true"},
                    json={"payload": yaml.safe_dump(after, allow_unicode=True)},
                    timeout=25,
                )
            except ClientError:
                self.route_health = previous
                controller.request(
                    "PUT",
                    "/configs",
                    params={"force": "true"},
                    json={"payload": yaml.safe_dump(before, allow_unicode=True)},
                    timeout=25,
                )
                raise
            for name, member in choices.items():
                try:
                    controller.select(name, member)
                except ClientError:
                    pass
            try:
                controller.recheck_routes(self.group_names, CONNECTIVITY_URL)
            except ClientError:
                self._record("event=route_quarantine_healthcheck_failed", "warning")

    @serialized
    def apply_profile(self, profile):
        """Replace routing in the live process, retaining listeners and TUN identity."""
        if not self.running or self.runtime is None:
            raise ClientError("Подключение завершилось до применения подписки.")
        before = quarantine_config(self._base_config, self.route_health.frozen)
        config = runtime_config(
            profile.config, self._session_settings, self.port, self.secret, profile.local_rules
        )
        for key in ("tun", "mixed-port", "ipv6"):
            if key in self._base_config:
                config[key] = copy.deepcopy(self._base_config[key])
        if isinstance(config.get("dns"), dict):
            previous_dns = self._base_config.get("dns", {})
            for key in ("fake-ip-range", "fake-ip-range6"):
                if key in previous_dns:
                    config["dns"][key] = previous_dns[key]
        # New provider paths cannot overwrite the running configuration's cache.
        token = secrets.token_hex(8)
        for section in ("proxy-providers", "rule-providers"):
            for index, provider in enumerate(config.get(section, {}).values()):
                provider["path"] = f"./providers/reload-{token}/{section}-{index}.cache"
        cache = RuleCache(self.root.parent, profile, config, self.runtime, self._record)
        if self._rule_cache:
            self._rule_cache.capture()
        cache.restore()
        candidate = self.runtime / "reload.yaml"
        atomic_write(candidate, yaml.safe_dump(config, allow_unicode=True).encode())
        try:
            validation = subprocess.run(
                [str(self._resolve_core()), "-d", str(self.runtime), "-f", str(candidate), "-t"],
                capture_output=True,
                timeout=25,
                creationflags=CREATE_FLAGS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ClientError(
                "Не удалось проверить новую конфигурацию. Прежний сеанс сохранён."
            ) from exc
        if validation.returncode:
            raise ClientError("Ядро отклонило новую конфигурацию. Прежний сеанс сохранён.")
        self._session_secrets = tuple(
            dict.fromkeys((*self._session_secrets, *profile_secrets(profile), self.secret))
        )
        with Controller(self.port, self.secret) as controller:
            current = controller.request("GET", "/configs")
            before["mode"] = current.get("mode", self._policy_mode)
            before["log-level"] = current.get("log-level", before["log-level"])
            config["log-level"] = before["log-level"]
            choices = {
                name: item.get("now")
                for name, item in controller.proxies().items()
                if item.get("type") == "Selector" and isinstance(item.get("now"), str)
            }

            def put(value):
                controller.request(
                    "PUT",
                    "/configs",
                    params={"force": "false"},
                    json={"payload": yaml.safe_dump(value, allow_unicode=True)},
                    timeout=30,
                )

            try:
                put(config)
                proxies = controller.proxies()
                for name, member in {**choices, **profile.selections}.items():
                    item = proxies.get(name, {})
                    if item.get("type") == "Selector" and member in item.get("all", []):
                        controller.select(name, member)
                if profile.forced_server:
                    controller.request("PATCH", "/configs", json={"mode": "global"})
                    controller.select("GLOBAL", profile.forced_server)
                # Fetch before committing state so any API failure triggers rollback.
                names = [g["name"] for g in config.get("proxy-groups", []) if not g.get("hidden")]
                if (profile.forced_server or config["mode"] == "global") and "GLOBAL" not in names:
                    names.insert(0, "GLOBAL")
                nodes = [node["name"] for node in config.get("proxies", [])]
                groups = controller.groups(names, nodes)
                atomic_write(
                    self.runtime / "config.yaml",
                    yaml.safe_dump(config, allow_unicode=True).encode(),
                )
            except (ClientError, OSError) as exc:
                try:
                    put(before)
                    for name, member in choices.items():
                        controller.select(name, member)
                except ClientError:
                    raise ClientError(
                        "Не удалось применить подписку и подтвердить откат. Проверьте подключение."
                    ) from exc
                raise ClientError(
                    "Обновление не применено. Прежняя конфигурация восстановлена."
                ) from exc
        self._base_config = copy.deepcopy(config)
        self._policy_mode = config["mode"]
        self.group_names, self.node_names = names, nodes
        self.forced_server = profile.forced_server
        self.route_health, self.frozen_routes = RouteHealth(), {}
        self._rule_cache = cache
        self._record("event=subscription_applied_live")
        return groups

    def restore_selections(self, selections: dict[str, str]) -> None:
        if not selections:
            return
        with Controller(self.port, self.secret) as controller:
            proxies = controller.proxies()
            for group, member in selections.items():
                item = proxies.get(group, {})
                if (
                    group in self.group_names
                    and item.get("type") == "Selector"
                    and member in item.get("all", [])
                ):
                    controller.select(group, member)

    def cancel_start(self):
        self._stop_requested.set()

    @serialized
    def stop(self, reason="requested") -> None:
        if self._rule_cache:
            self._rule_cache.capture()
        for event in self._connections.clear(reason):
            self._record(event, source="Connections")
        self.connection_log.close(reason, self._session_secrets)
        if self.process is not None or self.runtime is not None:
            pid = self.process.pid if self.process is not None else None
            self._record(
                f"event=stop_requested reason={reason} pid={pid} elapsed_ms={int((time.monotonic() - self._start_time) * 1000) if self._start_time else 0}"
            )
        # Restore before removing the listener; retain the journal on any failure.
        try:
            self.system_proxy.restore()
        finally:
            self._stop_process()

    def _stop_process(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self._record(f"event=process_terminate_sent pid={self.process.pid}")
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._record(f"event=process_kill_sent pid={self.process.pid}", "warning")
                    self.process.kill()
                    self.process.wait(timeout=5)
            self._record(
                f"event=process_stopped pid={self.process.pid} exit_code={self.process.poll()}"
            )
            self.process = None
        if self._reader:
            self._reader.join(timeout=1)
            self._reader = None
        if self.job:
            self.job.close()
            self.job = None
        if self.runtime:
            shutil.rmtree(self.runtime)
            self.runtime = None
        self._rule_cache = None
        self.secret = ""
        self.group_names = []
        self.route_health = RouteHealth()
        self.frozen_routes = {}
        self.node_names = []
        self._traffic_sample = None
        self.mixed_port = 0

    def _read_output(self, process, secrets):
        try:
            for line in process.stdout:
                level = next(
                    (
                        value
                        for value in ("error", "warning", "debug", "info")
                        if f"level={value}" in line
                    ),
                    "info",
                )
                clean = redact(line.strip(), secrets)
                self.trace.add(clean, level, "Mihomo", secrets)
                if "Start TUN listening error:" in clean:
                    self._startup_failure = "Не удалось создать адаптер TUN: " + clean.split(
                        "Start TUN listening error:", 1
                    )[1].rstrip('"')
                self._queue_log(level, clean)
        except (OSError, ValueError):
            pass
        finally:
            process.stdout.close()
            self._record(
                f"event=process_output_closed pid={process.pid} exit_code={process.poll()}"
            )

    def drain_logs(self, limit=150):
        with self._log_lock:
            values = [self._logs.popleft() for _ in range(min(limit, len(self._logs)))]
            if self._dropped_logs:
                values.insert(
                    0,
                    (
                        "warning",
                        f"Очередь интерфейса пропустила {self._dropped_logs} записей; полный поток — logs/network.jsonl.",
                    ),
                )
                self._dropped_logs = 0
            return values

    @serialized
    def set_log_level(self, level):
        if self.running:
            with Controller(self.port, self.secret) as controller:
                controller.request("PATCH", "/configs", json={"log-level": level})

    @serialized
    def flush_dns(self):
        if not self.running:
            raise ClientError("Сначала запустите подключение.")
        with Controller(self.port, self.secret) as controller:
            controller.request("POST", "/cache/dns/flush")
