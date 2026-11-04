import httpx
import pytest

from nodera import __version__
from nodera.bundled_core import core_manifest
from nodera.models import ClientError
from nodera.subscription import MAX_CONFIG_BYTES, fetch_profile, parse_config, validate_url

YAML = b"proxies:\n  - name: test\n    type: ss\n    server: example.com\n    port: 443\n"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/s/token",
        "file:///secret",
        "https://user:pw@example.com/s/t",
        "https://example.com:bad/s/t",
        "https://example.com/s/t#x",
        "https://example.com/a b",
    ],
)
def test_reject_unsafe_url(url):
    with pytest.raises(ClientError):
        validate_url(url)


def test_panel_contract_and_usage(monkeypatch):
    monkeypatch.setattr("nodera.subscription.windows_identity", lambda: ("Windows 11 Pro", "25H2"))

    def handle(request):
        assert request.headers["x-device-id"] == "persistent-id"
        assert request.headers["x-client-name"] == "Nodera"
        assert request.headers["x-device-os"] == "windows"
        assert request.headers["x-device-name"] == "Windows 11 Pro"
        assert request.headers["x-client-version"] == __version__
        assert request.headers["x-os-version"] == "25H2"
        assert request.headers["x-core-name"] == "mihomo"
        assert request.headers["x-core-version"] == core_manifest()["version"].removeprefix("v")
        assert "text/html" not in request.headers["accept"]
        return httpx.Response(
            200,
            content=YAML,
            headers={
                "Subscription-Userinfo": "upload=12; download=34; total=1024; expire=2000000000"
            },
        )

    profile = fetch_profile(
        "https://example.com/s/token", "PRO", "persistent-id", httpx.MockTransport(handle)
    )
    assert profile.config["proxies"][0]["name"] == "test"
    assert profile.usage == {"upload": 12, "download": 34, "total": 1024, "expire": 2000000000}


@pytest.mark.parametrize("status", [301, 302, 403, 404, 500])
def test_errors_do_not_expose_token(status):
    with pytest.raises(ClientError) as error:
        fetch_profile(
            "https://example.com/s/PRIVATE_TOKEN",
            "PRO",
            "id",
            httpx.MockTransport(lambda _: httpx.Response(status)),
        )
    assert "PRIVATE_TOKEN" not in str(error.value)


@pytest.mark.parametrize(
    "content",
    [
        b"<html>login</html>",
        b"[]",
        b"proxies: []",
        b"proxies: [broken]",
        b"!!python/object:builtins.object {}",
        b"a" * (MAX_CONFIG_BYTES + 1),
    ],
)
def test_invalid_config(content):
    with pytest.raises(ClientError):
        parse_config(content)


def test_reject_executable_provider():
    with pytest.raises(ClientError):
        parse_config(YAML + b"proxy-providers:\n  unsafe:\n    type: file\n    path: C:/secret\n")


def test_reject_oversized_download():
    with pytest.raises(ClientError):
        fetch_profile(
            "https://example.com/s/t",
            "PRO",
            "id",
            httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"a" * (MAX_CONFIG_BYTES + 1))
            ),
        )


@pytest.mark.parametrize(
    "extra",
    [
        b"proxy-groups: [invalid]",
        b"proxy-groups:\n  - name: test\n    type: select\n    proxies: [test]",
        b"proxy-groups:\n  - name: G\n    type: select\n    proxies: [123]",
        b"tun: true",
        b"dns: []",
    ],
)
def test_bad_group_or_network_shape_rejected_before_ui(extra):
    with pytest.raises(ClientError):
        parse_config(YAML + extra)


@pytest.mark.parametrize(
    "extra",
    [
        b"rules: null",
        b"rules: [{bad: data}]",
        b"mode: [rule]",
        b"x: &cycle {nested: *cycle}",
        b"x: !!set {one: null}",
        b'x: "\\uD800"',
    ],
)
def test_unusable_yaml_is_rejected_before_saving_or_rendering(extra):
    with pytest.raises(ClientError):
        parse_config(YAML + extra)


def test_yaml_dates_are_normalized_for_json_storage():
    import json

    config = parse_config(YAML + b"metadata: {created: 2026-11-03}\n")
    assert config["metadata"]["created"] == "2026-11-03"
    assert json.loads(json.dumps(config)) == config


def test_fetch_logging_identifies_failure_phase_without_exposing_link():
    events = []
    with pytest.raises(ClientError):
        fetch_profile(
            "https://example.test/PRIVATE_TOKEN",
            "P",
            "id",
            httpx.MockTransport(lambda _: httpx.Response(200, content=b"rules: nope")),
            log=lambda message, level="info": events.append(message),
        )
    assert any("subscription_body_received" in value for value in events)
    assert any("phase=parse" in value for value in events)
    assert not any("PRIVATE_TOKEN" in value for value in events)


def test_oversized_usage_header_does_not_crash_after_receiving_profile():
    profile = fetch_profile(
        "https://example.test/s/private",
        "P",
        "id",
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=YAML, headers={"Subscription-Userinfo": "total=" + "9" * 5000}
            )
        ),
    )
    assert profile.config["proxies"]
    assert "total" not in profile.usage


def test_broken_compressed_body_is_reported_without_crashing():
    class BrokenGzip(httpx.SyncByteStream):
        def __iter__(self):
            yield b"this is not gzip"

    with pytest.raises(ClientError, match="сжатый"):
        fetch_profile(
            "https://example.test/PRIVATE",
            "P",
            "id",
            httpx.MockTransport(
                lambda _: httpx.Response(
                    200, headers={"Content-Encoding": "gzip"}, stream=BrokenGzip()
                )
            ),
        )
