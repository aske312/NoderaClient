from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import QElapsedTimer, QTimer
from PySide6.QtTest import QTest

from nodera.models import ClientError, Profile, ProfileLibrary, Settings
from nodera.pro_provenance import TRUSTED_PRO_KEYS, config_digest, digest, is_verified_profile
from nodera.storage import Store
from nodera.subscription_history import restored_profile
from nodera.ui import MainWindow


def profile(name="old"):
    return Profile(
        "Subscription",
        "https://example.invalid/s/PRIVATE",
        {"proxies": [{"name": name, "type": "direct"}], "rules": ["MATCH,DIRECT"]},
        "2026-11-10T00:00:00+00:00",
        id="stable",
    )


def wait(app, window):
    deadline = QElapsedTimer()
    deadline.start()
    while (window.task or window.inspections) and deadline.elapsed() < 5000:
        app.processEvents()
        QTest.qWait(5)
    assert not window.task and not window.inspections


def make_window(tmp_path, app, library):
    store = Store(tmp_path)
    store.save_library(library)
    window = MainWindow(
        store,
        Settings(tun=False, update_hours=6, geo_auto=False, close_to_tray=False),
        library.active,
        library,
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    return window


def test_history_is_encrypted_and_survives_local_changes_activation_and_restart(tmp_path):
    old = profile()
    current = profile("new")
    library = ProfileLibrary([current], current.id).remember(old)
    old.config["rules"].append("DOMAIN,changed.example,REJECT")
    library = library.upsert(replace(current, name="Local name", local_rules=["MATCH,DIRECT"]))
    store = Store(tmp_path)
    store.save_library(library)
    assert b"PRIVATE" not in (tmp_path / "profiles.dpapi").read_bytes()
    loaded = Store(tmp_path).load_library()
    assert loaded.previous_profiles[current.id].config["rules"] == ["MATCH,DIRECT"]
    assert loaded.active.name == "Local name"
    store.delete_profile()
    assert not store.load_library().previous_profiles


def test_restore_keeps_access_usage_local_policy_and_verified_source():
    old = profile()
    current = replace(
        profile("new"),
        usage={"download": 1000, "total": 1000},
        access_state="exhausted",
        local_rules=["DOMAIN,local.example,DIRECT"],
        locations={"old": "US"},
        forced_server="new",
    )
    kid = next(iter(TRUSTED_PRO_KEYS))
    for item in (old, current):
        item.pro_verification = {
            "kid": kid,
            "verified_at": 1,
            "url_sha256": digest(item.url.encode()),
            "config_sha256": config_digest(item.config),
        }
    restored = restored_profile(current, old)
    assert restored.config == old.config and is_verified_profile(restored)
    assert restored.usage == current.usage and restored.access_state == "exhausted"
    assert restored.local_rules == current.local_rules and restored.locations == current.locations
    assert restored.auto_update_paused and restored.forced_server == ""
    assert restored.id == current.id and restored.url == current.url


def test_restore_cannot_drop_pinned_key_or_cross_subscription():
    current, old = profile("new"), profile()
    kid = next(iter(TRUSTED_PRO_KEYS))
    current.pro_verification = {
        "kid": kid,
        "verified_at": 1,
        "url_sha256": digest(current.url.encode()),
        "config_sha256": config_digest(current.config),
    }
    with pytest.raises(ClientError, match="ключом"):
        restored_profile(current, old)
    with pytest.raises(ClientError, match="другой"):
        restored_profile(current, replace(old, id="other"))


def test_remote_refresh_records_only_config_changes_and_failed_apply_preserves_history(
    tmp_path, monkeypatch, application_fonts
):
    original = profile()
    prior = profile("older")
    library = ProfileLibrary([original], original.id).remember(prior)
    window = make_window(tmp_path, application_fonts, library)
    monkeypatch.setattr(
        window, "fetch_subscription", lambda *a: replace(original, usage={"download": 1})
    )
    window.load_subscription(original.url, original.name)
    wait(application_fonts, window)
    assert window.library.previous_profiles[original.id].config == prior.config
    monkeypatch.setattr(window, "fetch_subscription", lambda *a: profile("new"))
    window.load_subscription(original.url, original.name)
    wait(application_fonts, window)
    assert window.library.previous_profiles[original.id].config == original.config
    saved = deepcopy(window.library)
    window.connected = True
    window.session_profile = window.profile
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    monkeypatch.setattr(window, "fetch_subscription", lambda *a: profile("broken"))

    def reject(*a):
        raise ClientError("Invalid config")

    monkeypatch.setattr(window.core, "apply_profile", reject)
    window.load_subscription(original.url, original.name)
    wait(application_fonts, window)
    assert window.store.load_library() == saved and window.library == saved
    assert errors


@pytest.mark.parametrize("active", [False, True])
def test_rollback_applies_only_to_target_and_pauses_auto_refresh(
    tmp_path, monkeypatch, application_fonts, active
):
    current, old = profile("new"), profile()
    other = replace(profile("other"), id="other", url="https://example.invalid/s/OTHER")
    library = ProfileLibrary([current, other], current.id if active else other.id).remember(old)
    window = make_window(tmp_path, application_fonts, library)
    window.preview_profile_id = current.id
    window.connected = True
    window.session_profile = library.active
    calls = []
    monkeypatch.setattr("nodera.modals.confirm", lambda *a, **kw: True)
    monkeypatch.setattr(window.core, "apply_profile", lambda restored: calls.append(restored) or [])
    window.rollback_card_profile()
    wait(application_fonts, window)
    restored = next(item for item in window.library.profiles if item.id == current.id)
    assert restored.config == old.config and restored.auto_update_paused
    assert window.library.active_id == library.active_id
    assert len(calls) == int(active)
    assert window.store.load_library() == window.library
    assert window.library.previous_profiles[current.id].config == current.config
    window.settings.update_hours = 1
    # Other profiles are fresh so only the paused target would otherwise qualify.
    window.profile_retry_at[other.id] = float("inf")
    window.update_retry_at = float("inf") if not active else 0
    monkeypatch.setattr(
        window, "load_subscription", lambda *a, **kw: pytest.fail("Auto-updated rolled-back config")
    )
    window.auto_update_profile()


def test_failed_live_rollback_preserves_disk_history_and_session(
    tmp_path, monkeypatch, application_fonts
):
    current, old = profile("new"), profile()
    library = ProfileLibrary([current], current.id).remember(old)
    window = make_window(tmp_path, application_fonts, library)
    window.connected = True
    window.session_profile = current
    monkeypatch.setattr("nodera.modals.confirm", lambda *a, **kw: True)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)

    def fail(*a):
        raise ClientError("Runtime refused rollback")

    monkeypatch.setattr(window.core, "apply_profile", fail)
    window.rollback_card_profile()
    wait(application_fonts, window)
    assert window.store.load_library() == library and window.library == library
    assert window.session_profile == current and window.connected
    assert errors


def test_manual_update_resumes_auto_updates_after_rollback(
    tmp_path, monkeypatch, application_fonts
):
    paused = replace(profile(), auto_update_paused=True)
    window = make_window(
        tmp_path, application_fonts, ProfileLibrary([paused], paused.id).remember(profile("new"))
    )
    monkeypatch.setattr(window, "fetch_subscription", lambda *a: profile("newer"))
    window.load_subscription(paused.url, paused.name)
    wait(application_fonts, window)
    assert not window.profile.auto_update_paused
    assert not window.store.load_profile().auto_update_paused


def test_save_failure_keeps_original_snapshot(tmp_path, monkeypatch, application_fonts):
    current, old = profile("new"), profile()
    library = ProfileLibrary([current], current.id).remember(old)
    window = make_window(tmp_path, application_fonts, library)
    monkeypatch.setattr("nodera.modals.confirm", lambda *a, **kw: True)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)

    def fail(_):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(window.store, "save_library", fail)
    window.rollback_card_profile()
    wait(application_fonts, window)
    assert window.library == library and window.store.load_library() == library
    assert errors
