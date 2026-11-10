import hashlib
import json
import sys

import pytest

from nodera.installer_bundle import check_command


def bundle_fixture(tmp_path):
    assets = tmp_path / "installer"
    resources = tmp_path / "nodera/resources"
    engine = tmp_path / "engine/Nodera-Engine.exe"
    assets.mkdir()
    resources.mkdir(parents=True)
    engine.parent.mkdir()
    engine.write_bytes(b"installation engine")
    (assets / "build-info.json").write_text(
        json.dumps({"version": "0.0.9", "channel": "release", "revision": "tested"})
    )
    (assets / "manifest.json").write_text(
        json.dumps({"engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest()})
    )
    for name in ("LICENSE", "license-ru.txt"):
        (assets / name).write_text("license")
    for name in ("nodera.ico", "nodera.svg"):
        (resources / name).write_bytes(b"icon")
    (resources / "pro-trusted-keys.json").write_text("{}")
    return resources, engine


@pytest.mark.parametrize("damage", [None, "missing PRO keys", "changed engine"])
def test_installer_check_verifies_runtime_resources_without_installing(
    tmp_path, monkeypatch, damage
):
    resources, engine = bundle_fixture(tmp_path)
    if damage == "missing PRO keys":
        (resources / "pro-trusted-keys.json").unlink()
    elif damage == "changed engine":
        engine.write_bytes(b"tampered")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(
        "nodera.install_service.find_installed",
        lambda: pytest.fail("Bundle check inspected the installed app"),
    )
    monkeypatch.setattr(
        "nodera.install_service.stop_client",
        lambda *args: pytest.fail("Bundle check stopped the client"),
    )
    monkeypatch.setattr(
        "nodera.system_proxy.SystemProxy.restore",
        lambda *args: pytest.fail("Bundle check changed the proxy"),
    )
    report = tmp_path / "report.json"
    assert check_command([str(report)]) == (0 if damage is None else 1)
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["ok"] is (damage is None)
    if damage is None:
        assert result["pro_keys_present"] and result["identity"]["revision"] == "tested"
    elif damage == "missing PRO keys":
        assert "pro-trusted-keys.json" in result["error"]
    else:
        assert "engine hash mismatch" in result["error"]


def test_installer_check_requires_report_destination():
    assert check_command([]) == 2
