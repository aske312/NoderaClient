# One build definition for local builds and CI.
import json
import hashlib
from pathlib import Path

root = Path(SPECPATH).resolve().parent
core = root / "vendor" / "mihomo"
manifest = root / "nodera" / "resources" / "mihomo.json"
build_metadata = root / "nodera/resources/build-info.json"
if not build_metadata.exists() or not (root / "build/version-info.txt").exists():
    raise RuntimeError("Run scripts/build.ps1 to generate build metadata before packaging")
meta = json.loads(manifest.read_text(encoding="utf-8"))
geodata = json.loads((manifest.parent / "geodata.json").read_text(encoding="utf-8"))
for item in geodata["files"] + geodata["notices"]:
    path = root / "vendor/geodata" / item["filename"]
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
            raise RuntimeError(f"Invalid embedded geodata: {path.name}; run prepare_core.py")
for filename, expected in (
    ("mihomo.exe", meta["binary_sha256"]),
    ("LICENSE", meta["license_sha256"]),
    (meta["source_filename"], meta["source_sha256"]),
):
    with (core / filename).open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise RuntimeError(f"Invalid embedded core component: {filename}; run prepare_core.py")

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "build/licenses"), "licenses"),
        # Preserve the upstream executable byte-for-byte for runtime SHA-256 checks.
        (str(core / "mihomo.exe"), "mihomo"),
        (str(manifest), "nodera/resources"),
        (str(root / "nodera/resources/pro-trusted-keys.json"), "nodera/resources"),
        (str(root / "nodera/resources/nodera.ico"), "nodera/resources"),
        (str(root / "nodera/resources/nodera.svg"), "nodera/resources"),
        (str(manifest.parent / "geodata.json"), "nodera/resources"),
        (str(root / "vendor/geodata"), "geodata"),
        (str(build_metadata), "nodera/resources"),
        (str(root / "nodera/resources/world-map.json"), "nodera/resources"),
        (str(root / "nodera/resources/flags"), "nodera/resources/flags"),
        (str(root / "nodera/resources/protocols"), "nodera/resources/protocols"),
        (str(root / "nodera/resources/flags-LICENSE.txt"), "nodera/resources"),
        (str(root / "nodera/resources/flags-NOTICE.txt"), "nodera/resources"),
        (str(root / "nodera/resources/map-NOTICE.txt"), "nodera/resources"),
        (str(core / "LICENSE"), "third_party/mihomo"),
        (str(core / "NOTICE.txt"), "third_party/mihomo"),
        (str(core / meta["source_filename"]), "third_party/mihomo"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Nodera",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=True,
    version=str(root / "build/version-info.txt"),
    icon=str(root / "nodera/resources/nodera.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Nodera")
