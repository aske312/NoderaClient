"""PRO-only numeric observations sent to the subscription server. No local AI."""

import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from nodera import __version__
from nodera.models import ClientError
from nodera.pro_provenance import is_verified_profile
from nodera.subscription import validate_url

INTERVAL = 30


def supported(profile):
    if (
        profile is None
        or profile.access_state
        or type(profile.reports_version) is not int
        or profile.reports_version != 1
        or not is_verified_profile(profile)
    ):
        return False
    try:
        reports_url(profile.url)
    except ClientError:
        return False
    return True


def reports_url(url):
    parsed = urlsplit(validate_url(url))
    parts = parsed.path.rstrip("/").split("/")
    if not parts[-1] or not (
        len(parts) == 3
        and parts[1] == "s"
        or len(parts) == 5
        and parts[1:4] == ["api", "mihomo", "subscriptions"]
    ):
        raise ClientError("Сервер подписки не поддерживает диагностику PRO.")
    return urlunsplit((parsed.scheme, parsed.netloc, "/".join(parts) + "/reports", "", ""))


def number(value, maximum=2**63 - 1):
    return value if type(value) is int and 0 <= value <= maximum else None


def build_report(session_id, counters, diagnostics, events=()):
    counters = counters or {}
    result = {
        "version": 1,
        "report_id": str(uuid4()),
        "session_id": session_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "client_version": __version__,
        "counters": {
            key: number(counters.get(key), 1_000_000 if key == "connections" else 2**63 - 1)
            for key in ("upload", "download", "connections")
        },
        "events": [],
        "diagnostics": {
            "state": diagnostics["state"],
            "mode": diagnostics["mode"],
            "network_check": diagnostics.get("network_check", "unverified"),
            "network_check_age_seconds": number(
                diagnostics.get("network_check_age_seconds"), 2**31 - 1
            ),
            "uptime_seconds": number(counters.get("uptime"), 2**31 - 1),
            "issues": [
                code
                for code in diagnostics.get("issues", [])
                if code
                in {
                    "core_start_failed",
                    "core_api_unavailable",
                    "https_probe_failed",
                    "ipv6_route_unavailable",
                }
            ][:4],
        },
    }
    version = re.search(r"v?\d+\.\d+\.[0-9A-Za-z.+_-]+", diagnostics.get("core_version", ""))
    result["diagnostics"]["core_version"] = version[0][:40] if version else ""
    for entry in events[-100:]:
        if entry.get("event") not in {"opened", "traffic", "closed"}:
            continue
        try:
            stamp = datetime.fromisoformat(entry["time"])
            if stamp.tzinfo is None:
                continue
            if stamp.timestamp() < diagnostics.get("since", float("-inf")):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        row = {"time": stamp.isoformat(), "event": entry["event"]}
        for key in ("upload", "download", "connections", "delta_upload", "delta_download"):
            value = number(entry.get(key), 1_000_000 if key == "connections" else 2**63 - 1)
            if value is not None:
                row[key] = value
        result["events"].append(row)
    return result


def send_report(profile, device_id, payload, *, proxy=None, transport=None):
    if not supported(profile):
        return "unsupported", INTERVAL
    if profile.reports_diagnostics is not True:
        payload = {key: value for key, value in payload.items() if key != "diagnostics"}
    # Reuse the application's device/HWID headers; no second device identity.
    from nodera.channel_selection import command_headers

    try:
        with httpx.Client(
            timeout=httpx.Timeout(8, connect=4),
            follow_redirects=False,
            trust_env=False,
            proxy=proxy,
            transport=transport,
        ) as client:
            with client.stream(
                "POST", reports_url(profile.url), headers=command_headers(device_id), json=payload
            ) as response:
                if response.status_code == 429:
                    retry = response.headers.get("retry-after", "30")
                    return "limited", min(300, max(15, int(retry))) if retry.isdigit() else INTERVAL
                if response.status_code in {301, 302, 303, 307, 308, 401, 403, 404, 405, 422}:
                    return "unsupported", INTERVAL
                if response.status_code != 200:
                    return "unavailable", INTERVAL
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        return "unknown", INTERVAL
                import json

                value = json.loads(raw)
                if (
                    not isinstance(value, dict)
                    or value.get("version") != 1
                    or value.get("accepted") is not True
                ):
                    return "unknown", INTERVAL
                return "sent", INTERVAL
    except (httpx.HTTPError, ValueError, ClientError):
        # An ambiguous response is never replayed. The next report is a new observation.
        return "unknown", INTERVAL


class ConnectionReporter:
    """One daemon worker and one replaceable pending observation; never block the core/UI."""

    def __init__(self, sender=send_report):
        self.sender = sender
        self.condition = threading.Condition()
        self.pending = None
        self.thread = None
        self.closed = False
        self.generation = 0
        self.key = None
        self.target = None
        self.session_id = ""
        self.next_at = 0.0
        self.blocked = None
        self.status = "disabled"
        self.sent_at = None

    def observe(self, profile, settings, epoch, counters, diagnostics, events=(), *, proxy=None):
        enabled = bool(
            profile and settings.client_reports.get(profile.id) is True and supported(profile)
        )
        with self.condition:
            if self.closed:
                return
            if not enabled:
                self.generation += 1
                self.pending = None
                self.key = None
                self.target = None
                self.blocked = None
                self.status = "disabled"
                self.condition.notify_all()
                return
            if diagnostics["state"] == "disconnected" and self.key is None:
                self.status = "waiting"
                return
            key = (profile.id, profile.url, epoch)
            if diagnostics["state"] != "connected" and self.key and self.key[:2] == key[:2]:
                key = self.key
            session_changed = self.key != key
            target_changed = (
                self.target is None
                or self.target.pro_verification != profile.pro_verification
                or self.target.url != profile.url
                or self.target.reports_diagnostics != profile.reports_diagnostics
            )
            if session_changed or target_changed:
                self.generation += 1
                self.pending = None
                self.blocked = None
                self.status, self.sent_at = "waiting", None
            if session_changed:
                self.key, self.session_id = key, str(uuid4())
            if target_changed:
                self.target = deepcopy(profile)
            if self.blocked == key or counters is None:
                return
            self.pending = (
                self.generation,
                self.target,
                settings.device_id,
                build_report(self.session_id, counters, diagnostics, events),
                proxy,
            )
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="nodera-reports", daemon=True)
                self.thread.start()
            self.condition.notify_all()

    def snapshot(self, profile_id=None):
        with self.condition:
            if profile_id is not None and (self.key is None or self.key[0] != profile_id):
                return "waiting", None
            return self.status, self.sent_at

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify_all()

    def _run(self):
        while True:
            with self.condition:
                while not self.closed:
                    delay = self.next_at - time.monotonic()
                    if self.pending is not None and delay <= 0:
                        break
                    self.condition.wait(max(0.01, delay) if self.pending else None)
                if self.closed:
                    return
                generation, profile, device_id, payload, proxy = self.pending
                self.pending = None
            try:
                status, delay = self.sender(profile, device_id, payload, proxy=proxy)
            except Exception:
                status, delay = "unknown", INTERVAL
            with self.condition:
                self.next_at = time.monotonic() + max(15, delay)
                if generation != self.generation:
                    continue
                self.status = status
                if status == "sent":
                    self.sent_at = datetime.now().strftime("%H:%M:%S")
                if status == "unsupported":
                    self.blocked = self.key
                    self.pending = None
                self.condition.notify_all()
