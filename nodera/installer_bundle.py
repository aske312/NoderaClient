"""Read-only checks of the frozen bootstrap, before installation can begin."""

import hashlib
import json
import sys
from pathlib import Path


def verify_bundle(bundle):
    bundle = Path(bundle)
    # Exercise the same delayed imports used while stopping the old client.
    # Importing these classes performs no process, registry or proxy operations.
    from nodera.installer_ui import InstallerWindow
    from nodera.system_proxy import SystemProxy

    assert InstallerWindow is not None and SystemProxy is not None
    keys = json.loads(
        (bundle / "nodera/resources/pro-trusted-keys.json").read_text(encoding="utf-8")
    )
    if not isinstance(keys, dict):
        raise ValueError("Invalid trusted PRO keys resource")
    assets = bundle / "installer"
    identity = json.loads((assets / "build-info.json").read_text(encoding="utf-8"))
    manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
    if identity.get("channel") != "release":
        raise ValueError("Expected a release installer")
    for resource in ("LICENSE", "license-ru.txt"):
        (assets / resource).read_text(encoding="utf-8")
    for resource in ("nodera.ico", "nodera.svg"):
        if not (bundle / "nodera/resources" / resource).is_file():
            raise FileNotFoundError(resource)
    with (bundle / "engine/Nodera-Engine.exe").open("rb") as engine:
        digest = hashlib.file_digest(engine, "sha256").hexdigest()
    if digest != manifest["engine_sha256"]:
        raise ValueError("Installer engine hash mismatch")
    return {"ok": True, "identity": identity, "engine_sha256": digest, "pro_keys_present": True}


def check_command(arguments):
    if len(arguments) != 1:
        return 2
    report = Path(arguments[0])
    try:
        bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        result = verify_bundle(bundle)
        code = 0
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        code = 1
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return code
