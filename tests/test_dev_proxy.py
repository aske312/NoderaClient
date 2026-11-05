from types import SimpleNamespace

import pytest

from nodera.app import development_subscription_proxy
from nodera.models import ClientError
from nodera.profiles_ui import ProfilesMixin


@pytest.mark.parametrize(
    "value",
    [
        "https://localhost:7890",
        "http://remote.example:7890",
        "http://localhost:0",
        "http://localhost:bad",
        "http://user:secret@localhost:7890",
        "http://localhost:7890/path",
    ],
)
def test_dev_proxy_rejects_invalid_addresses(value):
    with pytest.raises(ClientError):
        development_subscription_proxy(["--subscription-proxy=" + value])


def test_dev_proxy_requires_explicit_option():
    assert development_subscription_proxy(["--dev"]) is None
    assert (
        development_subscription_proxy(["--dev", "--subscription-proxy=http://127.0.0.1:7890"])
        == "http://127.0.0.1:7890"
    )


@pytest.mark.parametrize(
    "connected,tun,expected",
    [
        (False, False, "http://127.0.0.1:7890"),
        (True, False, "http://127.0.0.1:12345"),
        (True, True, None),
    ],
)
def test_subscription_uses_bootstrap_proxy_only_before_connection(
    monkeypatch, connected, tun, expected
):
    calls = []
    monkeypatch.setattr(
        "nodera.profiles_ui.fetch_profile", lambda *a, **kw: calls.append(kw["proxy"])
    )
    window = SimpleNamespace(
        connected=connected,
        session_tun=tun,
        subscription_proxy="http://127.0.0.1:7890",
        core=SimpleNamespace(mixed_port=12345),
        settings=SimpleNamespace(device_id="test"),
    )
    ProfilesMixin.fetch_subscription(window, "https://example.com/s/private", "Test")
    assert calls == [expected]
