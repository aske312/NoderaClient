import hashlib
import sys

import pytest

from nodera.bundled_core import bundled_location, resolve_core
from nodera.models import ClientError
from scripts.prepare_core import put_verified


def test_frozen_core_path_is_inside_application_not_working_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "application"), raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mihomo.exe").write_bytes(b"untrusted working-directory executable")
    assert bundled_location() == tmp_path / "application" / "mihomo" / "mihomo.exe"


def test_missing_embedded_core_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr("nodera.bundled_core.bundled_location", lambda: tmp_path / "missing.exe")
    with pytest.raises(ClientError, match="Переустановите"):
        resolve_core()


def test_modified_core_is_rejected_before_execution(tmp_path, monkeypatch):
    binary = tmp_path / "mihomo.exe"
    binary.write_bytes(b"changed")
    monkeypatch.setattr("nodera.bundled_core.bundled_location", lambda: binary)
    monkeypatch.setattr(
        "nodera.bundled_core.core_manifest",
        lambda: {"binary_sha256": hashlib.sha256(b"original").hexdigest()},
    )
    with pytest.raises(ClientError, match="повреждено"):
        resolve_core()
    binary.write_bytes(b"original")
    assert resolve_core() == binary


def test_failed_download_hash_does_not_replace_existing_component(tmp_path):
    target = tmp_path / "component"
    target.write_bytes(b"original")
    with pytest.raises(ValueError, match="SHA-256"):
        put_verified(target, b"altered", hashlib.sha256(b"original").hexdigest())
    assert target.read_bytes() == b"original"
