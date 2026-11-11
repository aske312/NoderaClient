import hashlib
import io
import json
import subprocess
import threading
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from nodera import core_updates
from nodera.core import Core
from nodera.core_updates import CoreUpdates, check_release
from nodera.models import ClientError, Profile, Settings


def release_fixture(content=None):
    if content is None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mihomo-windows-amd64-compatible.exe", b"MZverified core")
            archive.writestr("../../untrusted.exe", b"ignored")
        content = buffer.getvalue()
    version = "1.19.34"
    filename = f"mihomo-windows-amd64-compatible-v{version}.zip"
    data = {
        "tag_name": "v" + version,
        "prerelease": False,
        "draft": False,
        "assets": [
            {
                "name": filename,
                "browser_download_url": f"https://github.com/MetaCubeX/mihomo/releases/download/v{version}/{filename}",
                "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        ],
    }
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    return check_release("1.19.33", transport), content, data


def stage(manager, version, content):
    entry = {"version": version, "sha256": hashlib.sha256(content).hexdigest()}
    path = manager.root / version / entry["sha256"] / "mihomo.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return entry, path


def test_download_verifies_archive_and_version_without_changing_selection(tmp_path, monkeypatch):
    manager = CoreUpdates(tmp_path)
    release, content, _ = release_fixture()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[1:] == ["-v"]
        return SimpleNamespace(returncode=0, stdout=b"Mihomo Meta v1.19.34 windows amd64")

    monkeypatch.setattr(core_updates.subprocess, "run", run)
    entry = manager.download(
        release, httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    )
    assert manager.binary(entry).read_bytes() == b"MZverified core"
    assert not manager.state_path.exists()
    assert not (tmp_path / "untrusted.exe").exists()
    manager.activate(entry)
    assert manager.resolve() == manager.binary(entry)
    assert CoreUpdates(tmp_path).version() == "1.19.34"
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["digest", "url", "size", "alpha", "tag", "draft"])
def test_release_rejects_untrusted_metadata(change):
    _, _, data = release_fixture()
    asset = data["assets"][0]
    if change == "digest":
        asset["digest"] = None
    elif change == "url":
        asset["browser_download_url"] = "https://example.org/core.zip"
    elif change == "size":
        asset["size"] = 2**40
    elif change == "alpha":
        data["prerelease"] = True
    elif change == "draft":
        data["draft"] = True
    else:
        data["tag_name"] = "../../1.19.34"
    with pytest.raises(ClientError):
        check_release("1.19.33", httpx.MockTransport(lambda _: httpx.Response(200, json=data)))


@pytest.mark.parametrize("current", ["1.19.34", "1.20.0", "2.0.0"])
def test_no_downgrade(current):
    _, _, data = release_fixture()
    assert (
        check_release(current, httpx.MockTransport(lambda _: httpx.Response(200, json=data)))
        is None
    )


@pytest.mark.parametrize(
    "failure", ["hash", "size", "zip", "member", "version", "launch", "cancel"]
)
def test_failed_download_keeps_selected_core(tmp_path, monkeypatch, failure):
    manager = CoreUpdates(tmp_path)
    entry, old = stage(manager, "1.19.33", b"old")
    manager.activate(entry)
    if failure == "zip":
        release, content, _ = release_fixture(b"not a zip")
    elif failure == "member":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../../mihomo.exe", b"MZ")
        release, content, _ = release_fixture(buffer.getvalue())
    else:
        release, content, _ = release_fixture()
    if failure == "hash":
        content = b"x" * len(content)
    if failure == "size":
        content += b"extra"
    cancelled = threading.Event()
    if failure == "cancel":
        cancelled.set()

    def run(*args, **kwargs):
        if failure == "launch":
            raise OSError("Cannot execute")
        return SimpleNamespace(returncode=0, stdout=b"Mihomo v1.19.32")

    monkeypatch.setattr(core_updates.subprocess, "run", run)
    with pytest.raises(ClientError):
        manager.download(
            release,
            httpx.MockTransport(lambda _: httpx.Response(200, content=content)),
            cancelled=cancelled,
        )
    assert manager.resolve() == old
    assert manager.state()["active"] == entry


def test_selection_rollback_integrity_and_isolation(tmp_path, monkeypatch):
    manager = CoreUpdates(tmp_path / "release")
    fallback = tmp_path / "bundled.exe"
    fallback.write_bytes(b"bundled")
    monkeypatch.setattr(core_updates, "resolve_core", lambda: fallback)
    first, first_path = stage(manager, "1.19.34", b"first")
    second, second_path = stage(manager, "1.19.35", b"second")
    manager.activate(first)
    manager.confirm(first_path)
    manager.activate(second)
    assert manager.state()["previous"] == first
    assert manager.resolve() == second_path
    assert CoreUpdates(tmp_path / "test").resolve() == fallback
    manager.rollback()
    assert manager.resolve() == first_path
    manager.rollback()
    assert manager.resolve() == fallback
    manager.activate(first)
    manager.activate(second)
    second_path.write_bytes(b"corrupt")
    assert manager.resolve() == first_path
    first_path.write_bytes(b"corrupt")
    assert manager.resolve() == fallback


def test_untrusted_state_cannot_escape_core_directory(tmp_path, monkeypatch):
    manager = CoreUpdates(tmp_path)
    fallback = tmp_path / "bundled"
    monkeypatch.setattr(core_updates, "resolve_core", lambda: fallback)
    manager.save({"active": {"version": "../../outside", "sha256": "a" * 64}})
    assert manager.resolve() == fallback
    manager.state_path.write_text(json.dumps(["invalid"]))
    assert manager.resolve() == fallback


def test_rollback_skips_corrupted_previous_core(tmp_path, monkeypatch):
    manager = CoreUpdates(tmp_path)
    first, first_path = stage(manager, "1.19.34", b"first")
    second, second_path = stage(manager, "1.19.35", b"second")
    fallback = tmp_path / "bundled"
    monkeypatch.setattr(core_updates, "resolve_core", lambda: fallback)
    manager.activate(first)
    manager.activate(second)
    first_path.write_bytes(b"corrupt")
    assert manager.rollback(expected=second_path)
    assert manager.resolve() == fallback


@pytest.mark.parametrize(
    "failure",
    [
        ClientError("Rejected config"),
        OSError("Bad executable"),
        subprocess.TimeoutExpired("mihomo", 25),
    ],
)
def test_new_core_start_failure_rolls_back_and_retries_once(tmp_path, monkeypatch, failure):
    core = Core(tmp_path / "runtime")
    entry, new = stage(core.core_updates, "1.19.34", b"new")
    old = tmp_path / "old.exe"
    monkeypatch.setattr(core_updates, "resolve_core", lambda: old)
    core.core_updates.activate(entry)
    calls = []
    monkeypatch.setattr("nodera.core.choose_ports", lambda _: (1234, 5678))

    def start(profile, settings, binary):
        calls.append(binary)
        if binary == new:
            raise failure
        return "1.19.33"

    monkeypatch.setattr(core, "_start", start)
    profile = Profile("test", "https://example.org/s/test", {"proxies": []}, "")
    assert core.start(profile, Settings(tun=False)) == "1.19.33"
    assert calls == [new, old]
    assert core.binary == old
    assert not core.core_updates.state()["pending"]


def test_cancelled_start_does_not_rollback(tmp_path, monkeypatch):
    core = Core(tmp_path / "runtime")
    entry, new = stage(core.core_updates, "1.19.34", b"new")
    core.core_updates.activate(entry)
    monkeypatch.setattr("nodera.core.choose_ports", lambda _: (1234, 5678))

    def start(*args):
        core.cancel_start()
        raise ClientError("cancelled")

    monkeypatch.setattr(core, "_start", start)
    with pytest.raises(ClientError, match="cancelled"):
        core.start(Profile("test", "https://example.org/s/test", {}, ""), Settings(tun=False))
    assert core.core_updates.resolve() == new


def ui_window(tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from nodera.storage import Store
    from nodera.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        Store(tmp_path), Settings(update_hours=0, geo_auto=False, close_to_tray=False), None
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    window.core_update_timer = QTimer(window)
    window.core_update_timer.setSingleShot(True)
    return app, window


def wait_ui(app, window):
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtTest import QTest

    deadline = QElapsedTimer()
    deadline.start()
    while window.inspections and deadline.elapsed() < 5000:
        app.processEvents()
        QTest.qWait(5)
    assert not window.inspections


def test_update_notification_once_and_click_routes_to_core(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    app, window = ui_window(tmp_path)
    release, _, _ = release_fixture()
    notices, downloads = [], []
    monkeypatch.setattr(core_updates, "check_release", lambda *a, **kw: release)
    monkeypatch.setattr(window.tray, "isVisible", lambda: True)
    monkeypatch.setattr(QSystemTrayIcon, "supportsMessages", lambda: True)
    monkeypatch.setattr(window.tray, "showMessage", lambda *a: notices.append(a))
    monkeypatch.setattr(window, "install_core_update", lambda: downloads.append(True))
    window.check_core_updates()
    wait_ui(app, window)
    assert len(notices) == 1 and not downloads
    assert window.core_update_timer.interval() == 6 * 60 * 60 * 1000
    window.check_core_updates()
    wait_ui(app, window)
    assert len(notices) == 1
    window.show_update_notification()
    app.processEvents()
    assert "1.19.34" in window.core_update_offer.message.text()
    window.core_update_offer.reject()
    assert not downloads
    window.show_core_update_offer()
    app.processEvents()
    window.core_update_offer.action.click()
    assert downloads == [True]
    assert window.stack.currentWidget() == window.application_page


@pytest.mark.parametrize("connected", [False, True])
def test_install_and_rollback_do_not_interrupt_current_connection(tmp_path, monkeypatch, connected):
    app, window = ui_window(tmp_path)
    release, _, _ = release_fixture()
    window.available_core_release = release
    window.connected = connected
    window.running_core_version = "1.19.33"
    entry, binary = stage(window.core_manager, "1.19.34", b"new")
    monkeypatch.setattr(window.core_manager, "download", lambda *a, **kw: entry)
    monkeypatch.setattr(
        window.core, "stop", lambda *a: pytest.fail("Interrupted current connection")
    )
    window.install_core_update()
    wait_ui(app, window)
    assert window.core_manager.resolve() == binary
    assert window.connected == connected
    assert "следующем подключении" in window.core_update_status.text()
    assert window.available_core_release is None
    assert window.application_values["Ядро"] == (
        "Mihomo 1.19.34 · работает 1.19.33" if connected else "Mihomo 1.19.34"
    )
    window.rollback_core_update()
    assert window.core_manager.selected() is None
    assert window.connected == connected
    monkeypatch.undo()
    window.connected = False


def test_core_check_retry_and_failed_download_keep_available_update(tmp_path, monkeypatch):
    app, window = ui_window(tmp_path)

    def fail(*a, **kw):
        raise ClientError("Offline")

    monkeypatch.setattr(core_updates, "check_release", fail)
    window.check_core_updates()
    wait_ui(app, window)
    assert window.core_update_status.text() == "Offline"
    assert window.core_update_check.isEnabled()
    assert window.core_update_timer.interval() == 30 * 60 * 1000
    release, _, _ = release_fixture()
    window.available_core_release = release
    monkeypatch.setattr(window.core_manager, "download", fail)
    window.install_core_update()
    wait_ui(app, window)
    assert window.available_core_release == release
    assert window.core_update_install.isEnabled()
    assert window.core_manager.selected() is None
