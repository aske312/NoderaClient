import hashlib
import threading

import httpx
import pytest

from nodera.geodata import prepare_session_geodata
from nodera.geodata_updates import API_URL, ASSETS, GeodataUpdates, check_release
from nodera.models import ClientError, Settings
from nodera.storage import Store
from nodera.ui import MainWindow


def data_fixture(value=b"valid database"):
    assets, bodies = [], {}
    for filename, name in ASSETS.items():
        content = value + name.encode()
        url = f"https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/{name}"
        assets.append(
            {
                "name": name,
                "browser_download_url": url,
                "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
        bodies[url] = content
    return {"tag_name": "latest", "published_at": "2026-11-11T02:03:50Z", "assets": assets}, bodies


def transport_for(data, bodies):
    return httpx.MockTransport(
        lambda req: (
            httpx.Response(200, json=data)
            if str(req.url) == API_URL
            else httpx.Response(200, content=bodies[str(req.url)])
        )
    )


def release_fixture(value=b"valid database"):
    data, bodies = data_fixture(value)
    transport = transport_for(data, bodies)
    return check_release("bundled", transport), transport, data, bodies


def test_complete_verified_bundle_selection_restart_and_rollback(tmp_path, monkeypatch):
    manager = GeodataUpdates(tmp_path)
    release, transport, _, _ = release_fixture()
    validations = []
    monkeypatch.setattr(
        manager,
        "validate_databases",
        lambda folder: validations.append(sorted(p.name for p in folder.iterdir())),
    )
    entry = manager.download(release, transport)
    assert not manager.state_path.exists()
    manager.activate(entry)
    destination = tmp_path / "session"
    destination.mkdir()
    prepare_session_geodata(destination, root=tmp_path)
    assert {p.name for p in destination.iterdir()} == set(ASSETS)
    assert manager.identity() == release.identity
    assert GeodataUpdates(tmp_path).version() == release.version
    assert check_release(manager.identity(), transport) is None
    assert validations == [sorted(ASSETS)]
    original = (destination / "GeoIP.dat").read_bytes()
    second, second_transport, _, _ = release_fixture(b"new database")
    manager.activate(manager.download(second, second_transport))
    assert (destination / "GeoIP.dat").read_bytes() == original
    manager.rollback()
    assert manager.identity() == release.identity
    manager.rollback()
    assert manager.selected() is None


@pytest.mark.parametrize("change", ["missing", "digest", "url", "size", "prerelease", "path"])
def test_invalid_metadata_rejected(change):
    data, bodies = data_fixture()
    if change == "missing":
        data["assets"].pop()
    elif change == "digest":
        data["assets"][0]["digest"] = None
    elif change == "url":
        data["assets"][0]["browser_download_url"] = "https://example.org/db"
    elif change == "size":
        data["assets"][0]["size"] = 2**40
    elif change == "prerelease":
        data["prerelease"] = True
    else:
        data["tag_name"] = "../../outside"
    with pytest.raises(ClientError):
        check_release("bundled", transport_for(data, bodies))


@pytest.mark.parametrize("failure", ["hash", "size", "parse", "cancel"])
def test_failed_update_never_publishes_partial_bundle(tmp_path, monkeypatch, failure):
    manager = GeodataUpdates(tmp_path)
    release, transport, _, _ = release_fixture()
    monkeypatch.setattr(manager, "validate_databases", lambda _: None)
    manager.activate(manager.download(release, transport))
    previous = manager.state()
    new_release, _, data, bodies = release_fixture(b"new")
    first = next(iter(bodies))
    if failure == "hash":
        bodies[first] = b"x" * len(bodies[first])
    if failure == "size":
        bodies[first] += b"too large"
    if failure == "parse":

        def reject(_):
            raise ValueError("Mihomo rejected database")

        monkeypatch.setattr(manager, "validate_databases", reject)
    cancelled = threading.Event()
    if failure == "cancel":
        cancelled.set()
    with pytest.raises(ClientError):
        manager.download(new_release, transport_for(data, bodies), cancelled=cancelled)
    assert manager.state() == previous
    assert manager.identity() == release.identity
    assert not list(manager.root.glob("staging-*"))


def test_corrupted_selected_bundle_falls_back_whole_set_and_can_be_repaired(tmp_path, monkeypatch):
    manager = GeodataUpdates(tmp_path)
    release, transport, _, _ = release_fixture()
    monkeypatch.setattr(manager, "validate_databases", lambda _: None)
    entry = manager.download(release, transport)
    manager.activate(entry)
    (manager.folder(entry) / "GeoIP.dat").write_bytes(b"corrupt")
    assert manager.selected() is None
    assert manager.download(release, transport) == entry
    assert manager.selected() == entry
    assert GeodataUpdates(tmp_path / "other-installation").selected() is None


def test_database_tool_does_not_start_geolocation(tmp_path, monkeypatch, application_fonts):
    from PySide6.QtCore import QElapsedTimer, QTimer
    from PySide6.QtTest import QTest

    window = MainWindow(
        Store(tmp_path), Settings(update_hours=0, geo_auto=False, close_to_tray=False), None
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    monkeypatch.setattr(
        window, "check_locations", lambda **kw: pytest.fail("Database updater started geolocation")
    )
    release, transport, _, _ = release_fixture()
    monkeypatch.setattr("nodera.geodata_updates.check_release", lambda *a, **kw: release)
    monkeypatch.setattr(window.geodata_manager, "validate_databases", lambda _: None)
    original = window.geodata_manager.download
    monkeypatch.setattr(
        window.geodata_manager, "download", lambda value, **kw: original(value, transport)
    )

    def wait():
        deadline = QElapsedTimer()
        deadline.start()
        while window.inspections and deadline.elapsed() < 5000:
            application_fonts.processEvents()
            QTest.qWait(5)
        assert not window.inspections

    window.check_updates()
    wait()
    assert window.available_updates["geodata"] == release
    window.apply_updates()
    wait()
    assert window.geodata_manager.identity() == release.identity
    assert "следующем подключении" in window.update_status.text()
    window.rollback_geodata_update()
    wait()
    assert window.geodata_manager.selected() is None


def test_about_does_not_load_databases_or_start_network_checks(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Geodata verification must wait for an explicit action")

    monkeypatch.setattr(GeodataUpdates, "selected", unexpected)
    monkeypatch.setattr("nodera.geodata_updates.check_release", unexpected)
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.navigate(7)
    assert not window.inspections
    assert window.geo_client is None


def test_automatic_check_uses_database_selection_metadata_in_test_channel(
    tmp_path, monkeypatch, application_fonts
):
    from nodera.demo import DemoWindow

    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.app_updates_enabled = DemoWindow.app_updates_enabled
    release, _, _, _ = release_fixture()
    entry = {"version": release.version, "files": release.files}
    monkeypatch.setattr(window.geodata_manager, "state", lambda: {"active": entry})

    def unexpected(*args, **kwargs):
        pytest.fail("Test update check must not read database contents or offer the installer")

    monkeypatch.setattr(window.geodata_manager, "selected", unexpected)
    monkeypatch.setattr("nodera.updater.check_release", unexpected)
    checked = []
    monkeypatch.setattr(
        "nodera.geodata_updates.check_release", lambda identity, **kw: checked.append(identity)
    )
    window.check_updates(automatic=True)
    from PySide6.QtTest import QTest

    for _ in range(100):
        application_fonts.processEvents()
        if not window.inspections:
            break
        QTest.qWait(5)
    assert not window.inspections
    assert checked == [release.identity]


def test_route_details_and_database_controls_fit(tmp_path, application_fonts):
    from nodera.models import Profile

    profile = Profile(
        "test",
        "https://example.invalid/s/test",
        {"proxies": [{"name": "a", "type": "http", "server": "example.invalid"}]},
        "",
    )
    window = MainWindow(
        Store(tmp_path), Settings(update_hours=0, geo_auto=False, close_to_tray=False), profile
    )
    window.show()
    window.navigate(4)
    window.server_tabs.setCurrentIndex(1)
    application_fonts.processEvents()
    assert window.address_table.isVisible()
    assert window.address_table.columnWidth(0) >= 150
    assert window.geo_determine_button.isVisible()
    window.navigate(7)
    application_fonts.processEvents()
    assert window.update_check.isVisible()
    assert window.navigation.rect().contains(window.navigation.tools.geometry())
