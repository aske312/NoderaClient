import errno

import pytest

from nodera import diagnostics


@pytest.mark.parametrize(
    "source,expected",
    [
        ("fe80::1%12", False),
        ("::1", False),
        ("::", False),
        ("2001:db8::1234", True),
        ("fd00::1", True),
    ],
)
def test_ipv6_route_check_uses_local_source_without_sending_data(monkeypatch, source, expected):
    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def connect(self, endpoint):
            assert endpoint[1] == 53

        def getsockname(self):
            return (source, 1234, 0, 0)

        def send(self, *args):
            pytest.fail("The route check must not send data")

    monkeypatch.setattr(diagnostics.socket, "socket", lambda *args: Socket())
    assert diagnostics.ipv6_route_available() is expected


@pytest.mark.parametrize(
    "code,expected",
    [
        (errno.ENETUNREACH, False),
        (errno.EADDRNOTAVAIL, False),
        (errno.EAFNOSUPPORT, False),
        (errno.EACCES, None),
    ],
)
def test_unavailable_route_is_distinguished_from_inconclusive_check(monkeypatch, code, expected):
    def fail(*args):
        raise OSError(code, "network check")

    monkeypatch.setattr(diagnostics.socket, "socket", fail)
    assert diagnostics.ipv6_route_available() is expected


def test_disabled_ipv6_does_not_probe_and_warning_does_not_change_configuration(monkeypatch):
    monkeypatch.setattr(diagnostics, "ipv6_route_available", lambda: pytest.fail("disabled probe"))
    assert diagnostics.ipv6_route_check(False) is None
    monkeypatch.setattr(diagnostics, "ipv6_route_available", lambda: False)
    check = diagnostics.ipv6_route_check(True)
    assert check["state"] == "warning" and "Windows" in check["detail"]
