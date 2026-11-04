from pathlib import Path

import pytest

from nodera.models import ClientError


def pytest_configure(config):
    # pytest creates basetemp itself, but its parent may not exist on a clean checkout.
    if config.option.basetemp:
        Path(config.option.basetemp).parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def offline_automatic_client_location(monkeypatch):
    # Tests must never contact a public IP-location service through the UI timer.
    def unavailable():
        raise ClientError("Определение недоступно в тесте.")

    monkeypatch.setattr("nodera.settings_ui.detect_client_location", unavailable)


@pytest.fixture(autouse=True)
def isolate_system_startup(monkeypatch):
    monkeypatch.setattr("nodera.autostart.is_enabled", lambda: False)
    monkeypatch.setattr(
        "nodera.autostart.set_enabled",
        lambda _: pytest.fail("Unit test attempted to change Windows startup"),
    )
