"""Resolve and verify the exact Mihomo executable delivered with Nodera."""

import hashlib
import json
import re
import sys
from pathlib import Path

from nodera.models import ClientError


def core_manifest() -> dict:
    path = Path(__file__).parent / "resources" / "mihomo.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("version"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("binary_sha256", "")))
        ):
            raise ValueError
        return data
    except (OSError, ValueError) as exc:
        raise ClientError("Компоненты Nodera повреждены. Переустановите приложение.") from exc


def bundled_location() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "mihomo" / "mihomo.exe"
    return Path(__file__).resolve().parents[1] / "vendor" / "mihomo" / "mihomo.exe"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_core() -> Path:
    binary = bundled_location()
    try:
        if not binary.is_file():
            raise ClientError("Встроенное ядро Mihomo отсутствует. Переустановите Nodera.")
        if sha256(binary) != core_manifest()["binary_sha256"]:
            raise ClientError("Встроенное ядро Mihomo повреждено. Переустановите Nodera.")
    except OSError as exc:
        raise ClientError("Нет доступа к встроенному ядру. Переустановите Nodera.") from exc
    return binary
