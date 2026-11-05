import hashlib
from pathlib import Path

import pytest

from nodera.install_service import (
    Installed,
    InstallService,
    Plan,
    choose_plan,
    restore_data,
    snapshot_data,
    stop_client,
)
from nodera.models import ClientError


def payload(folder, content=b"new product"):
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "Nodera.exe"
    exe.write_bytes(content)
    uninstaller = folder / "unins000.exe"
    uninstaller.write_bytes(b"uninstaller")
    manifest = {
        "files": {
            "Nodera.exe": {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        }
    }
    return Installed(folder, "0.0.3", "abc123", uninstaller), manifest


@pytest.mark.parametrize(
    "version,revision,expected",
    [
        ("0.0.2", "abc123", "replace"),
        ("0.0.3", "def456", "replace"),
        ("0.0.3", "", "replace"),
        ("0.0.3", "abc123", "current"),
    ],
)
def test_version_and_commit_determine_replacement(tmp_path, version, revision, expected):
    installed, manifest = payload(tmp_path / "existing")
    installed = Installed(installed.folder, version, revision, installed.uninstaller)
    plan = choose_plan(
        installed, {"version": "0.0.3", "revision": "abc123"}, manifest, tmp_path / "new"
    )
    assert plan.action == expected and plan.folder == installed.folder


def test_matching_build_is_repaired_when_managed_files_are_damaged(tmp_path):
    installed, manifest = payload(tmp_path / "existing")
    (installed.folder / "Nodera.exe").write_bytes(b"tampered")
    assert (
        choose_plan(
            installed, {"version": "0.0.3", "revision": "abc123"}, manifest, tmp_path
        ).action
        == "repair"
    )


def test_missing_uninstaller_and_explicit_repair_do_not_report_current(tmp_path):
    installed, manifest = payload(tmp_path / "existing")
    identity = {"version": "0.0.3", "revision": "abc123"}
    assert (
        choose_plan(installed, identity, manifest, tmp_path, force_repair=True).action == "repair"
    )
    installed.uninstaller.unlink()
    assert choose_plan(installed, identity, manifest, tmp_path).action == "repair"


def test_new_install_uses_confirmed_destination(tmp_path):
    target = tmp_path / "selected" / "Nodera Client"
    assert choose_plan(None, {"version": "0.0.3"}, {"files": {}}, target) == Plan(
        "install", target, None
    )


def test_stop_holds_application_lock_and_restores_owned_proxy_state(tmp_path, monkeypatch):
    events = []
    root, folder = tmp_path / "data", tmp_path / "app"
    monkeypatch.setattr(
        "nodera.install_windows.stop_owned_processes", lambda exe: events.append(exe)
    )

    class Proxy:
        def __init__(self, directory):
            assert directory == root

        def restore(self):
            events.append("proxy restored")

    monkeypatch.setattr("nodera.system_proxy.SystemProxy", Proxy)
    lock, reconnect = stop_client(root, folder, lambda _: None)
    try:
        assert lock.isLocked() and reconnect is False
        assert events == [folder / "Nodera.exe", "proxy restored"]
    finally:
        lock.unlock()


def test_user_data_snapshot_preserves_ciphertext_and_settings_without_runtime(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    original = {"profiles.dpapi": b"encrypted-profile", "settings.json": b'{"device_id":"same"}'}
    for name, content in original.items():
        (root / name).write_bytes(content)
    (root / "runtime").mkdir()
    (root / "runtime/private.yaml").write_bytes(b"transient")
    backup = snapshot_data(root, tmp_path / "backups")
    assert not (backup / "runtime").exists()
    for name in original:
        (root / name).unlink()
    restore_data(backup, root)
    assert all((root / name).read_bytes() == content for name, content in original.items())


def test_preview_cannot_invoke_installation_or_create_destination(tmp_path, monkeypatch):
    root = tmp_path / "data"
    service = InstallService({}, {}, tmp_path / "missing.exe", root, preview=True)
    monkeypatch.setattr(
        "nodera.install_service.stop_client", lambda *args: pytest.fail("Preview stopped an app")
    )
    with pytest.raises(ClientError, match="Предпросмотр"):
        service.run(Plan("install", tmp_path / "product", None), True, False, lambda _: None)
    assert not root.exists() and not (tmp_path / "product").exists()


@pytest.mark.parametrize("fail_engine", [False, True])
def test_replacement_stops_then_backs_up_uninstalls_and_restores_data(
    tmp_path, monkeypatch, fail_engine
):
    installed, manifest = payload(tmp_path / "product", b"old")
    installed = Installed(installed.folder, "0.0.2", "old", installed.uninstaller)
    root = tmp_path / "data"
    root.mkdir()
    settings = root / "settings.json"
    settings.write_bytes(b"original settings")
    engine = tmp_path / "engine.exe"
    engine.write_bytes(b"engine")
    manifest["engine_sha256"] = hashlib.sha256(b"engine").hexdigest()
    service = InstallService({"version": "0.0.3", "revision": "new"}, manifest, engine, root)
    events = []

    class Lock:
        def unlock(self):
            events.append("unlock")

    def stop(*args):
        events.append("stop")
        return Lock(), True

    def execute(arguments, log):
        if Path(arguments[0]) == installed.uninstaller:
            events.append("uninstall")
            assert list((tmp_path / "NoderaClient-install-backups").glob("*/settings.json"))
            settings.unlink()  # Simulates even an old uninstaller that removes user data.
        else:
            events.append("startup")

    def install(*args):
        events.append("install")
        if fail_engine:
            raise ClientError("disk error")

    monkeypatch.setattr("ctypes.windll.shell32.IsUserAnAdmin", lambda: True)
    monkeypatch.setattr("nodera.install_service.find_installed", lambda: installed)
    monkeypatch.setattr("nodera.install_service.stop_client", stop)
    monkeypatch.setattr(service, "_execute", execute)
    monkeypatch.setattr(service, "_engine", install)
    if fail_engine:
        with pytest.raises(ClientError, match="Данные|данные"):
            service.run(Plan("replace", installed.folder, installed), True, False, lambda _: None)
    else:
        result = service.run(
            Plan("replace", installed.folder, installed), True, False, lambda _: None
        )
        assert result["reconnect"] is True
    assert events[:3] == ["stop", "uninstall", "install"]
    assert events[-1] == "unlock"
    assert settings.read_bytes() == b"original settings"


def test_repair_never_uninstalls_matching_build(tmp_path, monkeypatch):
    installed, manifest = payload(tmp_path / "product")
    engine = tmp_path / "engine.exe"
    engine.write_bytes(b"engine")
    manifest["engine_sha256"] = hashlib.sha256(b"engine").hexdigest()
    root = tmp_path / "data"
    root.mkdir()
    service = InstallService({"version": "0.0.3", "revision": "abc123"}, manifest, engine, root)
    calls = []
    monkeypatch.setattr("ctypes.windll.shell32.IsUserAnAdmin", lambda: True)
    monkeypatch.setattr("nodera.install_service.find_installed", lambda: installed)
    monkeypatch.setattr(
        "nodera.install_service.stop_client",
        lambda *a: (type("Lock", (), {"unlock": lambda _: None})(), False),
    )
    monkeypatch.setattr(service, "_engine", lambda *a: calls.append("repair"))
    monkeypatch.setattr(service, "_execute", lambda args, log: calls.append(args[1]))
    service.run(
        Plan("repair", installed.folder, installed), True, False, lambda _: None, force_repair=True
    )
    assert calls == ["repair", "--configure-autostart"]
