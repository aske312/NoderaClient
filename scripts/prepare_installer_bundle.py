"""Bind the product payload, file identities and hidden engine to one bootstrap."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def payload_manifest(source):
    files = {}
    private_names = {"settings.json", "geo-cache.json", "nodera.lock", "maintenance.lock"}
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name == "install-manifest.json":
            continue
        if path.suffix.lower() == ".dpapi" or path.name.lower() in private_names:
            raise ValueError(f"User data must never be distributed: {path.relative_to(source)}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files[path.relative_to(source).as_posix()] = {
            "size": path.stat().st_size,
            "sha256": digest,
        }
    return {"files": files}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--engine", type=Path)
    args = parser.parse_args()
    destination = ROOT / "build/installer-bundle"
    destination.mkdir(parents=True, exist_ok=True)
    if args.engine is None:
        manifest = payload_manifest(args.source)
        (args.source / "install-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    else:
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        with args.engine.open("rb") as stream:
            manifest["engine_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        (destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        shutil.copy2(args.engine, destination / "Nodera-Engine.exe")
    for source in (
        ROOT / "LICENSE",
        ROOT / "packaging/license-ru.txt",
        ROOT / "nodera/resources/build-info.json",
    ):
        shutil.copy2(source, destination / source.name)
    (destination / "preview.json").write_text('{"preview": true}', encoding="utf-8")
    version = (ROOT / "build/version-info.txt").read_text(encoding="utf-8")
    (destination / "version-info.txt").write_text(
        version.replace("Nodera.exe", "Nodera-Setup.exe"), encoding="utf-8"
    )
    print("Prepared installer bundle and payload identities")


if __name__ == "__main__":
    main()
