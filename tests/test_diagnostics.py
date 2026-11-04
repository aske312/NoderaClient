import socket

from nodera.diagnostics import preflight
from nodera.models import ClientError, Profile, Settings


def test_preflight_reports_missing_setup_and_tun_rights(monkeypatch):
    monkeypatch.setattr("nodera.diagnostics.is_admin", lambda: False)

    def missing():
        raise ClientError("Встроенное ядро отсутствует.")

    monkeypatch.setattr("nodera.diagnostics.resolve_core", missing)
    checks = preflight(Settings(mixed_port=45678), None)
    errors = [check for check in checks if check["state"] == "error"]
    assert {check["title"] for check in errors} >= {"Ядро Mihomo", "Подписка"}
    assert next(check for check in checks if check["title"] == "Права для TUN")["state"] == "info"


def test_preflight_occupied_port_and_running_session(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        settings = Settings(mixed_port=occupied.getsockname()[1], tun=False)
        profile = Profile("P", "https://panel.example/s/PRIVATE", {"proxies": [{"name": "A"}]}, "")
        stopped = preflight(settings, profile)
        running = preflight(settings, profile, running=True)
    assert next(check for check in stopped if check["title"] == "Локальный порт")["state"] == "ok"
    assert next(check for check in running if check["title"] == "Локальный порт")["state"] == "ok"
    assert "PRIVATE" not in str(stopped)
