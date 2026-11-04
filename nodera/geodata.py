"""Pinned local databases required by Mihomo's GEOIP/GEOSITE/IP-ASN rules."""

import json
import re
import shutil
import sys
from pathlib import Path

from nodera.bundled_core import sha256
from nodera.models import ClientError

DATABASES = {"Country.mmdb", "GeoIP.dat", "GeoSite.dat", "ASN.mmdb"}


def geodata_manifest():
    try:
        path = Path(__file__).parent / "resources/geodata.json"
        entries = json.loads(path.read_text(encoding="utf-8"))["files"]
        if (
            not isinstance(entries, list)
            or len(entries) != len(DATABASES)
            or {item["filename"] for item in entries} != DATABASES
            or any(not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in entries)
        ):
            raise ValueError
        return entries
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ClientError("Манифест геоданных повреждён. Переустановите Nodera.") from exc


def bundled_geodata():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "geodata"
    return Path(__file__).resolve().parents[1] / "vendor/geodata"


def prepare_session_geodata(destination):
    try:
        for item in geodata_manifest():
            source = bundled_geodata() / item["filename"]
            if not source.is_file() or sha256(source) != item["sha256"]:
                raise ClientError(
                    "Встроенные геоданные отсутствуют или повреждены. Переустановите Nodera."
                )
            shutil.copyfile(source, destination / item["filename"])
    except OSError as exc:
        raise ClientError(
            "Не удалось подготовить геоданные. Проверьте доступ к файлам Nodera."
        ) from exc
