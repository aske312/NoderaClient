"""Independent, verified stable Mihomo releases and per-installation selection."""

import hashlib
import io
import json
import os
import re
import subprocess
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from nodera.bundled_core import core_manifest, resolve_core, sha256
from nodera.models import ClientError
from nodera.storage import atomic_write
from nodera.updater import version_tuple

REPOSITORY = "MetaCubeX/mihomo"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_BINARY = 192 * 1024 * 1024
LOCK = threading.RLock()


@dataclass(frozen=True)
class CoreRelease:
    version: str
    url: str
    sha256: str
    size: int

    @property
    def filename(self):
        return f"mihomo-windows-amd64-compatible-v{self.version}.zip"


def check_release(current, transport=None, proxy=None):
    try:
        with httpx.Client(transport=transport, proxy=proxy, trust_env=False, timeout=12) as client:
            response = client.get(API_URL, headers={"User-Agent": "Nodera-Core-Updater"})
            if response.status_code in (403, 429):
                raise ClientError("GitHub временно ограничил проверку ядра. Повторите позже.")
            response.raise_for_status()
            data = response.json()
        tag = data["tag_name"]
        if data.get("draft") or data.get("prerelease"):
            raise ValueError("Not stable")
        if version_tuple(tag) <= version_tuple(current):
            return None
        version = tag.removeprefix("v")
        filename = f"mihomo-windows-amd64-compatible-v{version}.zip"
        asset = next(item for item in data["assets"] if item.get("name") == filename)
        url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"
        digest = asset.get("digest", "")
        if (
            asset.get("browser_download_url") != url
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or type(asset.get("size")) is not int
            or not 0 < asset["size"] <= MAX_ARCHIVE
        ):
            raise ValueError("Invalid asset")
        return CoreRelease(version, url, digest[7:], asset["size"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration, AttributeError) as exc:
        raise ClientError("Не удалось проверить стабильное обновление Mihomo в GitHub.") from exc


class CoreUpdates:
    def __init__(self, root: Path):
        self.root = Path(root) / "cores"
        self.state_path = self.root / "selection.json"

    def state(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, state):
        atomic_write(self.state_path, json.dumps(state).encode())

    def binary(self, entry):
        if not isinstance(entry, dict):
            raise ValueError("Invalid selection")
        version_tuple(entry["version"])
        digest = entry["sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid digest")
        path = self.root / entry["version"] / digest / "mihomo.exe"
        if sha256(path) != digest:
            raise ValueError("Core integrity check failed")
        return path

    def selected(self):
        """Resolve a valid selection; damaged downloads fall back without execution."""
        with LOCK:
            state = self.state()
            for key in ("active", "previous"):
                entry = state.get(key)
                if entry is None:
                    if key == "active":
                        return None
                    continue
                try:
                    self.binary(entry)
                    return entry
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            return None

    def version(self):
        entry = self.selected()
        return entry["version"] if entry else core_manifest()["version"].removeprefix("v")

    def resolve(self):
        entry = self.selected()
        return self.binary(entry) if entry else resolve_core()

    def activate(self, entry):
        with LOCK:
            self.binary(entry)
            previous = self.selected()
            if previous == entry:
                return
            self.save({"active": entry, "previous": previous, "pending": True})

    def rollback(self, expected=None):
        with LOCK:
            state = self.state()
            if expected is not None:
                if not state.get("pending") or self.selected() is None:
                    return False
                if self.resolve() != expected:
                    return False
            previous = state.get("previous")
            if previous:
                try:
                    self.binary(previous)
                except (OSError, ValueError, KeyError, TypeError):
                    previous = None
            self.save({"active": previous, "previous": None, "pending": False})
            return True

    def confirm(self, binary):
        with LOCK:
            state = self.state()
            if state.get("pending") and self.resolve() == binary:
                state["pending"] = False
                self.save(state)

    def download(self, release, transport=None, proxy=None, cancelled=None):
        """Stage immutable files. Selection changes only after the UI accepts the result."""
        try:
            if cancelled and cancelled.is_set():
                raise ClientError("Загрузка ядра отменена.")
            version_tuple(release.version)
            expected_url = (
                f"https://github.com/{REPOSITORY}/releases/download/v{release.version}/"
                + release.filename
            )
            if (
                release.url != expected_url
                or not re.fullmatch(r"[0-9a-f]{64}", release.sha256)
                or not 0 < release.size <= MAX_ARCHIVE
            ):
                raise ValueError("Invalid release")
            content = bytearray()
            with httpx.Client(
                transport=transport,
                proxy=proxy,
                trust_env=False,
                follow_redirects=True,
                timeout=httpx.Timeout(30, connect=10),
            ) as client:
                with client.stream("GET", release.url) as response:
                    response.raise_for_status()
                    if response.url.scheme != "https":
                        raise ValueError("Insecure redirect")
                    for chunk in response.iter_bytes():
                        if cancelled and cancelled.is_set():
                            raise ClientError("Загрузка ядра отменена.")
                        content.extend(chunk)
                        if len(content) > release.size or len(content) > MAX_ARCHIVE:
                            raise ValueError("Archive too large")
            if (
                len(content) != release.size
                or hashlib.sha256(content).hexdigest() != release.sha256
            ):
                raise ValueError("Archive SHA-256 mismatch")
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                member = archive.getinfo("mihomo-windows-amd64-compatible.exe")
                if not 0 < member.file_size <= MAX_BINARY:
                    raise ValueError("Invalid executable size")
                binary = archive.read(member)
            if not binary.startswith(b"MZ"):
                raise ValueError("Not a Windows executable")
            entry = {"version": release.version, "sha256": hashlib.sha256(binary).hexdigest()}
            target = self.root / entry["version"] / entry["sha256"] / "mihomo.exe"
            if cancelled and cancelled.is_set():
                raise ClientError("Загрузка ядра отменена.")
            if not target.exists() or sha256(target) != entry["sha256"]:
                atomic_write(target, binary)
            self.binary(entry)
            # Version check doesn't create a TUN adapter, proxy or network listeners.
            result = subprocess.run(
                [str(target), "-v"],
                capture_output=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output = result.stdout.decode("utf-8", errors="replace")
            if result.returncode or not re.search(
                rf"^Mihomo(?:\s+Meta)?\s+v?{re.escape(release.version)}(?=\s|$)",
                output,
                re.IGNORECASE,
            ):
                raise ValueError("Unexpected core version")
            atomic_write(
                target.with_name("NOTICE.txt"),
                (
                    f"Mihomo v{release.version}; unmodified upstream Windows executable.\n"
                    f"Release: https://github.com/{REPOSITORY}/releases/tag/v{release.version}\n"
                    f"Source: https://github.com/{REPOSITORY}/tree/v{release.version}\n"
                    f"License: GPL-3.0; https://github.com/{REPOSITORY}/blob/v{release.version}/LICENSE\n"
                ).encode(),
            )
            return entry
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            httpx.HTTPError,
            zipfile.BadZipFile,
            subprocess.SubprocessError,
        ) as exc:
            raise ClientError(
                "Ядро не обновлено: ошибка загрузки, проверки SHA-256 или запуска. Прежнее ядро сохранено."
            ) from exc
