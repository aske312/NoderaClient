import pytest

from nodera.device_info import windows_identity


@pytest.mark.parametrize(
    "product,build,kind,expected",
    [
        ("Windows 10 Pro", "26200", "Client", "Windows 11 Pro"),
        ("Windows 10 Home", "19045", "Client", "Windows 10 Home"),
        ("Windows 11 Pro", "26200", "Client", "Windows 11 Pro"),
        ("Windows Server 2025", "26100", "Server", "Windows Server 2025"),
    ],
)
def test_windows_edition_and_release(monkeypatch, product, build, kind, expected):
    monkeypatch.setattr(
        "nodera.device_info.windows_registry",
        lambda: dict(
            ProductName=product,
            CurrentBuildNumber=build,
            InstallationType=kind,
            DisplayVersion="25H2",
        ),
    )
    assert windows_identity() == (expected, "25H2")


def test_registry_unavailable(monkeypatch):
    monkeypatch.setattr("nodera.device_info.windows_registry", lambda: {})
    monkeypatch.setattr("nodera.device_info.platform.release", lambda: "11")
    monkeypatch.setattr("nodera.device_info.platform.version", lambda: "10.0.26200")
    assert windows_identity() == ("Windows 11", "10.0.26200")


def test_invalid_registry_values_do_not_break_http_headers(monkeypatch):
    monkeypatch.setattr(
        "nodera.device_info.windows_registry",
        lambda: {
            "ProductName": "Windows\r\n11 Pro",
            "DisplayVersion": "выпуск",
        },
    )
    monkeypatch.setattr("nodera.device_info.platform.version", lambda: "10.0.26200")
    assert windows_identity() == ("Windows 11 Pro", "10.0.26200")
