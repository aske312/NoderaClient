"""Verified, atomic sets of GeoIP/GeoSite/ASN databases from MetaCubeX."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import yaml

from nodera.bundled_core import sha256
from nodera.core_updates import CoreUpdates
from nodera.geodata import DATABASES, geodata_manifest
from nodera.models import ClientError
from nodera.storage import atomic_write

REPOSITORY = "MetaCubeX/meta-rules-dat"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSETS = {
    "Country.mmdb": "country.mmdb",
    "GeoIP.dat": "geoip.dat",
    "GeoSite.dat": "geosite.dat",
    "ASN.mmdb": "GeoLite2-ASN.mmdb",
}
MAX_FILE = 128 * 1024 * 1024


def fingerprint(files):
    content = sorted((item["filename"], item["sha256"]) for item in files)
    return hashlib.sha256(json.dumps(content).encode()).hexdigest()


@dataclass(frozen=True)
class GeodataRelease:
    version: str
    files: list[dict]

    @property
    def identity(self):
        return fingerprint(self.files)


def validate_entry(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
        raise ValueError("Invalid database set")
    files = entry["files"]
    if len(files) != 4 or {item["filename"] for item in files} != DATABASES:
        raise ValueError("Incomplete database set")
    if any(not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in files):
        raise ValueError("Invalid database digest")
    return fingerprint(files)


def check_release(current, transport=None, proxy=None):
    try:
        with httpx.Client(transport=transport, proxy=proxy, trust_env=False, timeout=15) as client:
            response = client.get(API_URL, headers={"User-Agent": "Nodera-Geodata-Updater"})
            if response.status_code in (403, 429):
                raise ClientError("GitHub временно ограничил проверку геоданных. Повторите позже.")
            response.raise_for_status()
            data = response.json()
        tag = data["tag_name"]
        if data.get("draft") or data.get("prerelease") or not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
            raise ValueError("Invalid database release")
        version = datetime.fromisoformat(data["published_at"].replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        files = []
        for filename, asset_name in ASSETS.items():
            asset = next(item for item in data["assets"] if item.get("name") == asset_name)
            url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset_name}"
            if (
                asset.get("browser_download_url") != url
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest", ""))
                or type(asset.get("size")) is not int
                or not 0 < asset["size"] <= MAX_FILE
            ):
                raise ValueError("Invalid database asset")
            files.append(
                {
                    "filename": filename,
                    "url": url,
                    "sha256": asset["digest"][7:],
                    "size": asset["size"],
                }
            )
        release = GeodataRelease(version, files)
        return release if release.identity != current else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration, AttributeError) as exc:
        raise ClientError("Не удалось проверить обновления баз маршрутизации в GitHub.") from exc


class GeodataUpdates:
    def __init__(self, root):
        self.store_root = Path(root)
        self.root = self.store_root / "geodata"
        self.state_path = self.root / "selection.json"

    def state(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def folder(self, entry):
        return self.root / "bundles" / validate_entry(entry)

    def verify(self, entry):
        folder = self.folder(entry)
        if any(sha256(folder / item["filename"]) != item["sha256"] for item in entry["files"]):
            raise ValueError("Database integrity check failed")
        return folder

    def selected(self):
        state = self.state()
        if state.get("active") is None:
            return None
        for entry in (state.get("active"), state.get("previous")):
            try:
                self.verify(entry)
                return entry
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def identity(self):
        entry = self.selected()
        return validate_entry(entry) if entry else fingerprint(geodata_manifest())

    def version(self):
        entry = self.selected()
        return entry["version"] if entry else "Встроенные базы"

    def activate(self, entry):
        self.verify(entry)
        previous = self.selected()
        if entry != previous:
            atomic_write(
                self.state_path, json.dumps({"active": entry, "previous": previous}).encode()
            )

    def rollback(self):
        previous = self.state().get("previous")
        try:
            self.verify(previous)
        except (OSError, ValueError, KeyError, TypeError):
            previous = None
        atomic_write(self.state_path, json.dumps({"active": previous, "previous": None}).encode())

    def prepare(self, destination):
        entry = self.selected()
        if entry is None:
            return False
        folder = self.verify(entry)
        for item in entry["files"]:
            target = destination / item["filename"]
            shutil.copyfile(folder / item["filename"], target)
            if sha256(target) != item["sha256"]:
                raise ClientError("Не удалось проверить копию геоданных для подключения.")
        return True

    def download(self, release, transport=None, proxy=None, cancelled=None):
        entry = {"version": release.version, "files": release.files}
        temporary = None
        try:
            validate_entry(entry)
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix="staging-", dir=self.root))
            with httpx.Client(
                transport=transport,
                proxy=proxy,
                trust_env=False,
                follow_redirects=True,
                headers={"User-Agent": "Nodera-Geodata-Updater"},
                timeout=httpx.Timeout(30, connect=10),
            ) as client:
                for item in release.files:
                    if cancelled and cancelled.is_set():
                        raise ClientError("Загрузка геоданных отменена.")
                    if not 0 < item["size"] <= MAX_FILE or not re.fullmatch(
                        rf"https://github\.com/{REPOSITORY}/releases/download/[A-Za-z0-9_.-]+/{re.escape(ASSETS[item['filename']])}",
                        item["url"],
                    ):
                        raise ValueError("Invalid database download")
                    digest, size = hashlib.sha256(), 0
                    with (
                        client.stream("GET", item["url"]) as response,
                        (temporary / item["filename"]).open("wb") as stream,
                    ):
                        response.raise_for_status()
                        if response.url.scheme != "https":
                            raise ValueError("Insecure redirect")
                        for chunk in response.iter_bytes():
                            if cancelled and cancelled.is_set():
                                raise ClientError("Загрузка геоданных отменена.")
                            size += len(chunk)
                            if size > item["size"] or size > MAX_FILE:
                                raise ValueError("Database exceeds expected size")
                            stream.write(chunk)
                            digest.update(chunk)
                    if size != item["size"] or digest.hexdigest() != item["sha256"]:
                        raise ValueError("Database SHA-256 mismatch")
            self.validate_databases(temporary)
            if cancelled and cancelled.is_set():
                raise ClientError("Загрузка геоданных отменена.")
            atomic_write(temporary / "manifest.json", json.dumps(entry).encode())
            target = self.folder(entry)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                os.replace(temporary, target)
                temporary = None
            else:
                try:
                    self.verify(entry)
                except (OSError, ValueError):
                    for item in entry["files"]:
                        os.replace(temporary / item["filename"], target / item["filename"])
                    atomic_write(target / "manifest.json", json.dumps(entry).encode())
            self.verify(entry)
            return entry
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            httpx.HTTPError,
            subprocess.SubprocessError,
        ) as exc:
            raise ClientError(
                "Базы не обновлены: ошибка загрузки, SHA-256 или проверки Mihomo. Прежние базы сохранены."
            ) from exc
        finally:
            if (
                temporary is not None
                and temporary.parent.resolve() == self.root.resolve()
                and not temporary.is_symlink()
                and not temporary.is_junction()
            ):
                shutil.rmtree(temporary)

    def validate_databases(self, folder):
        binary = CoreUpdates(self.store_root).resolve()
        config = {
            "mixed-port": 0,
            "geo-auto-update": False,
            "rules": [
                "GEOSITE,cn,DIRECT",
                "GEOIP,CN,DIRECT,no-resolve",
                "IP-ASN,13335,DIRECT",
                "MATCH,DIRECT",
            ],
        }
        check = folder / "validate.yaml"
        try:
            for mode in (True, False):
                atomic_write(check, yaml.safe_dump({**config, "geodata-mode": mode}).encode())
                result = subprocess.run(
                    [
                        str(binary.resolve()),
                        "-d",
                        str(folder.resolve()),
                        "-f",
                        str(check.resolve()),
                        "-t",
                    ],
                    capture_output=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if result.returncode:
                    raise ValueError("Mihomo rejected downloaded geodata")
        finally:
            check.unlink(missing_ok=True)
