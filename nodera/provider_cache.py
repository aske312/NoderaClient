"""Encrypted rule-provider snapshots survive disposable Mihomo sessions."""

import hashlib
import json
import math
import os
import struct
import time

from nodera.models import ClientError
from nodera.storage import atomic_write, protect

MAX_RULE_BYTES = 16 * 1024 * 1024


class RuleCache:
    def __init__(self, root, profile, config, runtime, log=lambda message: None):
        self.directory = root / "rule-cache"
        self.entries = []
        self.seen = {}
        self.log = log
        runtime = runtime.resolve()
        for name, provider in config.get("rule-providers", {}).items():
            if provider.get("type") != "http":
                continue
            identity = {
                "profile": profile.id,
                **{
                    key: provider.get(key)
                    for key in ("url", "behavior", "format", "header", "headers")
                },
            }
            key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            target = (runtime / provider["path"]).resolve()
            if not target.is_relative_to(runtime):
                raise ClientError("Путь кэша правил выходит за каталог сеанса.")
            self.entries.append((name, self.directory / f"{key}.dpapi", target))

    def restore(self):
        for name, cache, target in self.entries:
            if not cache.is_file():
                self.log(f"event=rule_cache_miss provider={name}")
                continue
            try:
                if cache.stat().st_size > MAX_RULE_BYTES + 4096:
                    raise ValueError("cache exceeds limit")
                raw = protect(cache.read_bytes(), decrypt=True)
                modified = struct.unpack("<d", raw[:8])[0]
                payload = raw[8:]
                if (
                    not payload
                    or len(payload) > MAX_RULE_BYTES
                    or not math.isfinite(modified)
                    or not 0 < modified <= time.time() + 300
                ):
                    raise ValueError("invalid cache")
                atomic_write(target, payload)
                os.utime(target, (modified, modified))
                state = target.stat()
                self.seen[name] = (state.st_mtime_ns, state.st_size)
                self.log(
                    f"event=rule_cache_restored provider={name} bytes={len(payload)} age_s={max(0, int(time.time() - modified))}"
                )
            except (OSError, ValueError, ClientError, struct.error):
                self.log(
                    f"event=rule_cache_restore_failed provider={name}; core will fetch the source"
                )

    def capture(self):
        for name, cache, source in self.entries:
            try:
                if not source.is_file():
                    continue
                state = source.stat()
                marker = (state.st_mtime_ns, state.st_size)
                if self.seen.get(name) == marker or not 0 < state.st_size <= MAX_RULE_BYTES:
                    continue
                payload = source.read_bytes()
                if len(payload) != state.st_size or source.stat().st_mtime_ns != state.st_mtime_ns:
                    continue  # Retry after the core finishes updating the file.
                atomic_write(cache, protect(struct.pack("<d", state.st_mtime) + payload))
                self.seen[name] = marker
                self.log(f"event=rule_cache_saved provider={name} bytes={len(payload)}")
            except (OSError, ClientError):
                self.log(f"event=rule_cache_save_failed provider={name}")
        try:
            if self.directory.is_dir():
                files = sorted(
                    self.directory.glob("*.dpapi"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
                for path in files[32:]:
                    path.unlink()
        except OSError:
            pass
