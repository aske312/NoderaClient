"""Build-time preparation of pinned upstream binaries, license and source archive.

This prepares the bundled fallback. Runtime updates live separately in user data.
"""

import hashlib
import http.client
import io
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "nodera" / "resources" / "mihomo.json"
DESTINATION = PROJECT_ROOT / "vendor" / "mihomo"
MAX_DOWNLOAD = 128 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put_verified(path: Path, content: bytes, expected: str) -> None:
    if digest(content) != expected:
        raise ValueError(f"SHA-256 mismatch for {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def download(url: str, path: Path, expected: str) -> bytes:
    if path.exists() and digest(path.read_bytes()) == expected:
        return path.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": "Nodera-build"})
    for attempt in range(3):
        try:
            print(f"Downloading {path.name} (attempt {attempt + 1}/3)", flush=True)
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(MAX_DOWNLOAD + 1)
            break
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
        ) as exc:
            if (
                isinstance(exc, urllib.error.HTTPError)
                and exc.code not in {408, 429, 500, 502, 503, 504}
            ) or attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))
    if len(data) > MAX_DOWNLOAD:
        raise ValueError(f"Download exceeds limit: {path.name}")
    put_verified(path, data, expected)
    return data


def main() -> None:
    meta = json.loads(MANIFEST.read_text(encoding="utf-8"))
    archive = download(meta["url"], DESTINATION / meta["asset"], meta["archive_sha256"])
    with zipfile.ZipFile(io.BytesIO(archive)) as release:
        # Read only the pinned member; never extract arbitrary archive paths.
        binary = release.read(meta["executable"])
    target = DESTINATION / "mihomo.exe"
    if not target.exists() or digest(target.read_bytes()) != meta["binary_sha256"]:
        put_verified(target, binary, meta["binary_sha256"])
    download(meta["license_url"], DESTINATION / "LICENSE", meta["license_sha256"])
    download(meta["source_url"], DESTINATION / meta["source_filename"], meta["source_sha256"])
    geodata = json.loads((MANIFEST.parent / "geodata.json").read_text(encoding="utf-8"))
    for item in geodata["files"] + geodata["notices"]:
        download(item["url"], PROJECT_ROOT / "vendor/geodata" / item["filename"], item["sha256"])
    notice = (
        f"Mihomo {meta['version']} ({meta['platform']})\n"
        "Upstream: https://github.com/MetaCubeX/mihomo\n"
        f"Release: https://github.com/MetaCubeX/mihomo/releases/tag/{meta['version']}\n"
        "License: GPL-3.0; see LICENSE in this directory.\n"
        f"Unmodified upstream source archive: {meta['source_filename']}\n"
        "The standalone Mihomo process is distributed with Nodera.\n"
    )
    (DESTINATION / "NOTICE.txt").write_text(notice, encoding="utf-8")
    print(f"Prepared embedded Mihomo {meta['version']}; all upstream SHA-256 checks passed.")
    print("Prepared embedded geodata; all upstream SHA-256 checks passed.")


if __name__ == "__main__":
    main()
