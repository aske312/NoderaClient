import hashlib

import pytest

from scripts.prepare_installer_bundle import payload_manifest


@pytest.mark.parametrize(
    "name", ["profiles.dpapi", "profile.dpapi", "settings.json", "geo-cache.json"]
)
def test_installer_refuses_user_data_even_in_nested_payload(tmp_path, name):
    folder = tmp_path / "_internal" / "accidental-data"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(b"private")
    with pytest.raises(ValueError, match="User data must never be distributed"):
        payload_manifest(tmp_path)


def test_clean_payload_manifest_binds_only_product_files(tmp_path):
    (tmp_path / "Nodera.exe").write_bytes(b"product")
    (tmp_path / "install-manifest.json").write_text("old manifest")
    assert payload_manifest(tmp_path) == {
        "files": {"Nodera.exe": {"size": 7, "sha256": hashlib.sha256(b"product").hexdigest()}}
    }
