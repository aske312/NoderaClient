import os

import pytest

from nodera.models import ClientError
from nodera.uninstall import cleanup_user_data, handle_uninstall


def test_full_cleanup_removes_profiles_and_backups_preserving_other_data(tmp_path):
    for name in ("NoderaClient", "NoderaClient-install-backups", "NoderaClient-Test", "OtherApp"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "profiles.dpapi").write_bytes(b"private")
    cleanup_user_data(tmp_path, profiles=True, other_data=True)
    assert not (tmp_path / "NoderaClient").exists()
    assert not (tmp_path / "NoderaClient-install-backups").exists()
    assert (tmp_path / "NoderaClient-Test/profiles.dpapi").read_bytes() == b"private"
    assert (tmp_path / "OtherApp/profiles.dpapi").read_bytes() == b"private"


def test_cleanup_rejects_redirected_root_before_removing_any_data(tmp_path, monkeypatch):
    folder = tmp_path / "NoderaClient"
    folder.mkdir()
    (folder / "profiles.dpapi").write_bytes(b"keep")
    monkeypatch.setattr(type(folder), "is_junction", lambda path: path.name.endswith("-backups"))
    with pytest.raises(ClientError, match="перенаправлена"):
        cleanup_user_data(tmp_path, profiles=True, other_data=True)
    assert (folder / "profiles.dpapi").read_bytes() == b"keep"


def test_cleanup_does_not_follow_nested_directory_link(tmp_path):
    outside = tmp_path / "OtherApp"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    root = tmp_path / "NoderaClient"
    root.mkdir()
    try:
        os.symlink(outside, root / "linked", target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable")
    cleanup_user_data(tmp_path, profiles=True, other_data=True)
    assert (outside / "keep").read_text() == "keep"


def test_unrelated_arguments_cannot_trigger_uninstall():
    assert handle_uninstall(["--remove-autostart"]) is None
    assert handle_uninstall(["--finish-uninstall=clean", "--dev"]) is None


@pytest.mark.parametrize(
    "frozen,metadata",
    [
        (False, {"channel": "release"}),
        (True, {"channel": "test"}),
        (True, {}),
        (True, {"channel": "unknown"}),
    ],
)
def test_cleanup_requires_explicit_frozen_release_metadata(monkeypatch, frozen, metadata):
    monkeypatch.setattr("nodera.uninstall.sys.frozen", frozen, raising=False)
    monkeypatch.setattr("nodera.versioning.build_info", lambda: metadata)
    monkeypatch.setattr(
        "nodera.uninstall.QApplication", lambda *args: pytest.fail("Blocked cleanup initialized UI")
    )
    monkeypatch.setattr(
        "nodera.install_service.stop_client",
        lambda *args: pytest.fail("Blocked cleanup stopped a client"),
    )
    assert handle_uninstall(["--finish-uninstall=clean"]) == 1


@pytest.mark.parametrize("profiles,other_data", [(False, True), (True, False), (False, False)])
def test_profile_removal_is_independent_of_cache_cleanup(tmp_path, profiles, other_data):
    for name in ("NoderaClient", "NoderaClient-install-backups/old"):
        folder = tmp_path / name
        folder.mkdir(parents=True)
        (folder / "profiles.dpapi").write_bytes(b"encrypted profiles")
        (folder / "profile.dpapi").write_bytes(b"legacy profile")
        (folder / "settings.json").write_text("settings")
    cleanup_user_data(tmp_path, profiles=profiles, other_data=other_data)
    for name in ("NoderaClient", "NoderaClient-install-backups/old"):
        folder = tmp_path / name
        assert (folder / "profiles.dpapi").exists() is not profiles
        assert (folder / "profile.dpapi").exists() is not profiles
        assert (folder / "settings.json").exists() is not other_data
        if not profiles:
            assert (folder / "profiles.dpapi").read_bytes() == b"encrypted profiles"
