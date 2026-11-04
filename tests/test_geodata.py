import hashlib
import os

import pytest

from nodera.bundled_core import bundled_location
from nodera.core import Core
from nodera.geodata import DATABASES, prepare_session_geodata
from nodera.models import ClientError, Profile, Settings


@pytest.mark.parametrize("tampered", [False, True])
def test_missing_or_modified_database_is_rejected(tmp_path, monkeypatch, tampered):
    source, destination = tmp_path / "source", tmp_path / "session"
    source.mkdir()
    destination.mkdir()
    entries = []
    for filename in sorted(DATABASES):
        (source / filename).write_bytes(b"verified")
        entries.append({"filename": filename, "sha256": hashlib.sha256(b"verified").hexdigest()})
    target = source / entries[0]["filename"]
    if tampered:
        target.write_bytes(b"corrupted")
    else:
        target.unlink()
    monkeypatch.setattr("nodera.geodata.bundled_geodata", lambda: source)
    monkeypatch.setattr("nodera.geodata.geodata_manifest", lambda: entries)
    with pytest.raises(ClientError, match="геоданные"):
        prepare_session_geodata(destination)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
@pytest.mark.parametrize("dat_mode", [False, True])
def test_geo_rules_start_without_downloading_databases(tmp_path, dat_mode):
    profile = Profile(
        "Offline geodata",
        "",
        {
            "proxies": [{"name": "Local", "type": "direct"}],
            "geodata-mode": dat_mode,
            "geox-url": {
                key: "http://127.0.0.1:1/unavailable" for key in ("geoip", "geosite", "mmdb", "asn")
            },
            "rules": [
                "GEOSITE,cn,DIRECT",
                "GEOIP,RU,DIRECT,no-resolve",
                "IP-ASN,13335,DIRECT",
                "MATCH,DIRECT",
            ],
        },
        "",
    )
    core = Core(tmp_path)
    runtime = None
    try:
        core.start(profile, Settings(tun=False, system_proxy=False))
        runtime = core.runtime
        assert core.running
        assert all((runtime / name).is_file() for name in DATABASES)
        assert core.status()["uptime"] >= 0
    finally:
        core.stop()
    assert runtime is not None and not runtime.exists()
    assert not any("start download" in line.lower() for _, line in core.drain_logs(500))
