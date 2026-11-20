from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nodera import channel_selection
from nodera.models import ClientError, Profile, ProfileLibrary, Settings
from nodera.storage import Store
from nodera.ui import MainWindow


def test_hidden_selection_cannot_send_new_requests():
    assert channel_selection.SELECTION_ENABLED is False
    profile = Profile("PRO", "https://example.com/s/test", {}, "")
    with pytest.raises(ClientError):
        channel_selection.request_candidates(profile, "device")
    for action in (None, "accept"):
        with pytest.raises(ClientError):
            channel_selection.change_server_selection(SimpleNamespace(), "device", action)


def test_hidden_controls_preserve_profiles_and_normal_actions(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "PRO", "https://example.com/s/test", {"proxies": [{"name": "route", "type": "ss"}]}, ""
    )
    settings = Settings(
        geo_auto=False, update_hours=0, channel_preferences={profile.id: {"max_channels": 7}}
    )
    store = Store(tmp_path)
    library = ProfileLibrary([profile], profile.id)
    store.save_library(library)
    original = (tmp_path / "profiles.dpapi").read_bytes()
    monkeypatch.setattr(MainWindow, "is_pro_profile", lambda self, value: value is not None)
    window = MainWindow(store, settings, profile, library)
    monkeypatch.setattr(window, "run_task", Mock())
    monkeypatch.setattr(window.channel_dialog, "exec", Mock())
    try:
        assert window.profile_options.isHidden()
        assert window.dashboard_options_button.isHidden()
        assert "auto_routes" not in window.channel_dialog.options
        window.channel_dialog.show_option("auto_routes")
        assert window.channel_dialog.pages.currentWidget() is window.channel_dialog.overview
        window.channel_dialog.start.click()
        window.start_channel_selection()
        window.apply_ready_channels()
        window.channel_options()
        window.run_task.assert_not_called()
        window.channel_dialog.exec.assert_not_called()
        assert window.update_button.isEnabled()
        assert window.settings.channel_preferences == settings.channel_preferences
        assert (tmp_path / "profiles.dpapi").read_bytes() == original
    finally:
        window.exit_app()
        app.processEvents()
