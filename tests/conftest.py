from pathlib import Path

import pytest

from nodera.models import ClientError


def pytest_configure(config):
    # pytest creates basetemp itself, but its parent may not exist on a clean checkout.
    if config.option.basetemp:
        Path(config.option.basetemp).parent.mkdir(parents=True, exist_ok=True)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    root = item.funcargs.get("tmp_path")
    if report.failed and root:
        for path in (root / "logs/network.jsonl", root.parent / "logs/network.jsonl"):
            if path.is_file():
                report.sections.append(
                    ("Nodera core log: " + str(path), path.read_text(encoding="utf-8")[-20000:])
                )


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


@pytest.fixture(autouse=True)
def offline_automatic_route_locations(monkeypatch):
    # Lifecycle checks are verified separately without contacting public GeoIP services.
    monkeypatch.setattr("nodera.workbench.WorkbenchMixin.schedule_location_check", lambda *a: None)
