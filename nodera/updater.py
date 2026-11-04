"""Verified Windows installers from the product's public GitHub Releases."""

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from nodera import __version__
from nodera.models import ClientError

REPOSITORY = "aske312/NoderaClient"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_INSTALLER = 512 * 1024 * 1024
INSTALLER_NAME = "Nodera-Setup.exe"


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value
    ):
        raise ValueError("Invalid release version")
    return tuple(map(int, value.removeprefix("v").split(".")))


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int

    @property
    def filename(self):
        return INSTALLER_NAME


def check_release(current=__version__, transport=None, proxy=None):
    try:
        with httpx.Client(transport=transport, proxy=proxy, trust_env=False, timeout=12) as client:
            response = client.get(
                API_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Nodera-Updater",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if response.status_code == 404:
                raise ClientError("В GitHub пока нет доступного стабильного релиза.")
            response.raise_for_status()
            data = response.json()
        tag = data["tag_name"]
        if data.get("draft") or data.get("prerelease"):
            raise ValueError("Not a stable release")
        if version_tuple(tag) <= version_tuple(current):
            return None
        version = tag.removeprefix("v")
        filename = INSTALLER_NAME
        asset = next(item for item in data["assets"] if item.get("name") == filename)
        digest = asset.get("digest", "")
        expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"
        if (
            asset.get("browser_download_url") != expected_url
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or type(asset.get("size")) is not int
            or not 0 < asset["size"] <= MAX_INSTALLER
        ):
            raise ValueError("Invalid release asset")
        return Release(version, expected_url, digest[7:], asset["size"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise ClientError(
            "Не удалось проверить обновление в GitHub или проверить данные установщика."
        ) from exc


def download_release(release, root: Path, transport=None, proxy=None, cancelled=None):
    version_tuple(release.version)
    target = root / "updates" / release.version / release.filename
    temporary = target.with_suffix(".download")
    try:
        if cancelled and cancelled.is_set():
            raise ClientError("Загрузка отменена при завершении приложения.")
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        with (
            httpx.Client(
                transport=transport,
                proxy=proxy,
                trust_env=False,
                follow_redirects=True,
                timeout=httpx.Timeout(30, connect=10),
            ) as client,
            client.stream("GET", release.url) as response,
        ):
            response.raise_for_status()
            if response.url.scheme != "https":
                raise ValueError("Insecure redirect")
            with temporary.open("wb") as stream:
                for chunk in response.iter_bytes():
                    if cancelled and cancelled.is_set():
                        raise ClientError("Загрузка отменена при завершении приложения.")
                    size += len(chunk)
                    if size > release.size or size > MAX_INSTALLER:
                        raise ValueError("Installer exceeds expected size")
                    digest.update(chunk)
                    stream.write(chunk)
        if size != release.size or digest.hexdigest() != release.sha256:
            raise ValueError("Installer integrity check failed")
        os.replace(temporary, target)
        return target
    except (OSError, ValueError, httpx.HTTPError) as exc:
        raise ClientError(
            "Установщик не загружен или его SHA-256 не совпадает. Установка отменена."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def launch_installer(path, release):
    # Recheck at execution time, even if the file has been replaced after download.
    try:
        if path.stat().st_size != release.size:
            raise ValueError
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != release.sha256:
                raise ValueError
        subprocess.Popen([str(path), "/NORESTART"], cwd=path.parent)
    except (OSError, ValueError) as exc:
        raise ClientError("Не удалось запустить проверенный установщик обновления.") from exc
