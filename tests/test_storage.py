import json
import os

import pytest

from nodera.models import ClientError, Profile
from nodera.storage import Store


def test_device_identity_survives_restart(tmp_path):
    store = Store(tmp_path)
    assert store.load_settings().device_id == Store(tmp_path).load_settings().device_id


def test_close_to_tray_is_enabled_for_new_and_migrated_settings(tmp_path):
    assert Store(tmp_path / "new").load_settings().close_to_tray
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"device_id": "existing", "close_to_tray": False}))
    store = Store(tmp_path)
    migrated = store.load_settings()
    assert migrated.close_to_tray and migrated.tray_behavior_version == 1
    assert json.loads(path.read_text())["close_to_tray"] is True
    migrated.close_to_tray = False
    store.save_settings(migrated)
    assert not Store(tmp_path).load_settings().close_to_tray


def test_invalid_legacy_tray_setting_is_not_silently_overwritten(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"close_to_tray": "invalid"}))
    with pytest.raises(ClientError):
        Store(tmp_path).load_settings()


def test_legacy_local_mode_preserved_and_new_install_uses_vpn(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"device_id": "legacy", "core_path": "", "mixed_port": 7890, "tun": False})
    )
    legacy = Store(tmp_path).load_settings()
    assert legacy.tun is False
    assert "core_path" not in json.loads(path.read_text())
    assert Store(tmp_path / "new").load_settings().tun is True


@pytest.mark.parametrize("vpn", [False, True])
def test_old_subscription_mode_is_removed_without_changing_local_preference(tmp_path, vpn):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"device_id": "existing", "tun": vpn, "follow_profile": True}))
    assert Store(tmp_path).load_settings().tun is vpn
    assert "follow_profile" not in json.loads(path.read_text())


def test_corrupt_settings_not_silently_reset(tmp_path):
    (tmp_path / "settings.json").write_text('{"mixed_port": "bad"}')
    with pytest.raises(ClientError):
        Store(tmp_path).load_settings()


@pytest.mark.parametrize("content", ["[]", "null", '"invalid"'])
def test_settings_migration_rejects_wrong_document_type(tmp_path, content):
    (tmp_path / "settings.json").write_text(content)
    with pytest.raises(ClientError):
        Store(tmp_path).load_settings()


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_profile_encrypted_roundtrip_and_delete(tmp_path):
    store = Store(tmp_path)
    profile = Profile("PRO", "https://example.com/s/PRIVATE", {"proxies": []}, "now")
    store.save_profile(profile)
    assert b"PRIVATE" not in (tmp_path / "profiles.dpapi").read_bytes()
    assert store.load_profile() == profile
    store.delete_profile()
    assert store.load_profile() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_bad_ciphertext_is_recoverable_error(tmp_path):
    (tmp_path / "profile.dpapi").write_bytes(b"bad")
    with pytest.raises(ClientError):
        Store(tmp_path).load_profile()
