from pathlib import Path

import pytest

from nodera.models import ClientError


@pytest.fixture(scope="session", autouse=True)
def application_fonts():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from nodera.theme import prepare_fonts

    app = QApplication.instance() or QApplication([])
    prepare_fonts(app)
    yield app


@pytest.fixture(autouse=True)
def close_test_windows(application_fonts, monkeypatch):
    """Dispose each test's Qt windows after their normal asynchronous shutdown."""
    yield
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QElapsedTimer, QEvent
    from PySide6.QtTest import QTest

    roots = [w for w in application_fonts.topLevelWidgets() if w.parent() is None]
    for window in roots:
        if getattr(window, "shutdown_emitted", False):
            continue
        if hasattr(window, "exit_app"):
            type(window).exit_app(window)
        else:
            window.close()
    deadline = QElapsedTimer()
    deadline.start()
    while any(
        shiboken6.isValid(w)
        and (
            getattr(w, "task", None)
            or getattr(w, "inspections", None)
            or getattr(w, "poll_task", None)
            or getattr(w, "geo_task", None)
            or getattr(w, "worker", None)
        )
        for w in roots
    ):
        assert deadline.elapsed() < 10000, "Qt window did not complete its shutdown"
        application_fonts.processEvents()
        QTest.qWait(10)
    for window in roots:
        if shiboken6.isValid(window):
            window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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
