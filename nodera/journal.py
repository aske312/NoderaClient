"""Bounded structured logs; credentials are removed before memory and disk storage."""

import json
import re
import threading
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return call


def profile_secrets(profile):
    result = []

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if any(
                    word in str(key).lower()
                    for word in (
                        "password",
                        "secret",
                        "uuid",
                        "private-key",
                        "token",
                        "authorization",
                        "username",
                        "preshared-key",
                        "pre-shared-key",
                        "auth",
                    )
                ) and isinstance(item, str):
                    result.append(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    if profile:
        result.append(profile.url)
        visit(profile.config)
    return tuple(item for item in result if item)


def redact(message, secrets=()):
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(message))
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = (
                text.replace(secret, "[скрыто]")
                if len(secret) >= 6
                else re.sub(r"(?<!\w)" + re.escape(secret) + r"(?!\w)", "[скрыто]", text)
            )
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9_+/=.\-]+", r"\1 [скрыто]", text)
    text = re.sub(
        r"(?i)([\"\']?(?:password|passwd|secret|token|uuid|private-key|preshared-key|pre-shared-key|authorization|auth|auth-str|username|api-key)[\"\']?\s*[:=]\s*)[\"\']?[^\s,;\"\'}]+",
        r"\1[скрыто]",
        text,
    )
    text = re.sub(r"(https?://)([^\s/@]+:[^\s/@]+@)", r"\1[скрыто]@", text)
    text = re.sub(r"(https?://[^/\s?\"']+)(?:/[^\s\"']*|\?[^\s\"']+)", r"\1/[скрыто]", text)
    return text[:8000]


class Journal:
    def __init__(
        self,
        root: Path,
        limit=2000,
        persist=True,
        filename="events.jsonl",
        max_bytes=2 * 1024 * 1024,
    ):
        self._lock = threading.RLock()
        self.max_bytes = max_bytes
        self.root = root / "logs"
        self.path = self.root / filename
        self.entries = deque(maxlen=limit)
        self.persist = persist
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        except OSError:
            lines = []
        for line in lines:
            try:
                entry = json.loads(line)
                if isinstance(entry, dict) and all(
                    isinstance(entry.get(key), str)
                    for key in ("time", "level", "source", "message")
                ):
                    entry["message"] = redact(entry["message"])
                    self.entries.append(entry)
            except ValueError:
                continue

    @locked
    def configure(self, limit, persist):
        self.entries = deque(self.entries, maxlen=limit)
        self.persist = persist

    @locked
    def add(self, message, level="info", source="Nodera", secrets=()):
        entry = {
            "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "level": level if level in {"debug", "info", "warning", "error"} else "info",
            "source": source,
            "message": redact(message, secrets),
        }
        self._append(entry)
        return entry

    @locked
    def record(self, event, fields, secrets=()):
        def clean(value):
            if isinstance(value, str):
                return redact(value, secrets)
            if isinstance(value, list):
                return [clean(item) for item in value]
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            return value

        entry = {
            "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "event": event,
            **clean(fields),
        }
        self._append(entry)
        return entry

    def _append(self, entry):
        self.entries.append(entry)
        if self.persist:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    for index in (3, 2, 1):
                        previous = self.root / (
                            self.path.name if index == 1 else f"{self.path.stem}.{index - 1}.jsonl"
                        )
                        target = self.root / f"{self.path.stem}.{index}.jsonl"
                        if previous.exists():
                            previous.replace(target)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass

    @locked
    def clear(self):
        self.entries.clear()
        for name in (self.path.name, *(f"{self.path.stem}.{i}.jsonl" for i in (1, 2, 3))):
            (self.root / name).unlink(missing_ok=True)


def format_entry(entry):
    return (
        f"{entry['time'][11:19]}  {entry['level'].upper():7}  {entry['source']}  {entry['message']}"
    )


def useful_event(entry):
    if entry["level"] in {"warning", "error"}:
        return True
    if entry["level"] == "debug" or entry["source"] == "Соединения":
        return False
    if entry["source"] in {"Mihomo", "Ядро", "Подписки"} and "event=" in entry["message"]:
        return False
    if entry["source"] == "Mihomo":
        return False
    return True


def compact_entries(entries):
    result = {}
    counts = {}
    for entry in entries:
        key = (entry["time"][:16], entry["level"], entry["source"], entry["message"])
        count = counts.get(key, 0) + 1
        counts[key] = count
        result.pop(key, None)
        result[key] = {
            **entry,
            "message": entry["message"] + (f" (повторено {count} раз)" if count > 1 else ""),
        }
    return list(result.values())
