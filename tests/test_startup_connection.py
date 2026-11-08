"""Startup connection choices without launching a core or changing Windows settings."""

from unittest.mock import Mock

import pytest

from nodera import app
from nodera.models import ClientError, Profile, ProfileLibrary, Settings


@pytest.mark.parametrize(
    "auto_connect,arguments,resume,has_profile,broken_library,connect",
    [
        (False, [], False, True, False, False),
        (True, [], False, True, False, True),
        (True, ["--autostart"], False, True, False, True),
        (False, ["--autostart"], False, True, False, False),
        (False, ["--connect"], False, True, False, True),
        (False, [], True, True, False, True),
        (True, [], False, False, False, False),
        (False, ["--connect"], False, False, False, False),
        (True, [], False, True, True, False),
    ],
)
def test_startup_connects_only_when_requested_with_a_valid_active_profile(
    tmp_path, monkeypatch, auto_connect, arguments, resume, has_profile, broken_library, connect
):
    profile = Profile("Test", "https://example.invalid/s/test", {}, "", id="test")
    library = ProfileLibrary([profile], profile.id) if has_profile else ProfileLibrary()
    store = Mock()
    store.load_settings.return_value = Settings(auto_connect=auto_connect)
    store.load_library.return_value = library
    if broken_library:
        store.load_library.side_effect = ClientError("Cannot load profiles")
    window = Mock()
    monkeypatch.setattr(app.sys, "argv", ["nodera", *arguments])
    monkeypatch.setattr(app, "handle_command", lambda _: None)
    monkeypatch.setattr(app, "build_info", lambda: {})
    monkeypatch.setattr(app, "set_app_identity", Mock())
    monkeypatch.setattr(app, "QApplication", Mock())
    monkeypatch.setattr(app, "app_icon", Mock())
    monkeypatch.setattr(app, "ensure_admin", lambda: True)
    monkeypatch.setattr(app, "wait_for_previous_instance", Mock())
    monkeypatch.setattr(app, "data_directory", lambda **_: tmp_path)
    monkeypatch.setattr(app, "secure_directory", Mock())
    monkeypatch.setattr(app, "QLockFile", Mock())
    monkeypatch.setattr(app, "acquire_instance", lambda *_: resume)
    monkeypatch.setattr(app, "SystemProxy", Mock())
    monkeypatch.setattr(app, "Store", lambda _: store)
    monkeypatch.setattr(app, "MainWindow", Mock(return_value=window))
    monkeypatch.setattr(app, "RestartServer", Mock())
    timer = Mock()
    monkeypatch.setattr(app, "QTimer", timer)

    app.main()

    if connect:
        timer.singleShot.assert_called_once_with(0, window.toggle)
    else:
        timer.singleShot.assert_not_called()
    if broken_library:
        window.error.assert_called_once_with("Cannot load profiles")
