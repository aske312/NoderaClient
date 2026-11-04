import httpx
import pytest

from nodera.controller import TEST_URL, Controller
from nodera.models import ClientError

GROUP = "PRO / Выбор?#"
NODE = "Узел / один?#"


def proxy_data(kind="Selector"):
    return {
        "proxies": {
            GROUP: {"type": kind, "now": NODE, "all": [NODE, "B"]},
            NODE: {"type": "VLESS", "history": [{"delay": 120}]},
            "B": {"type": "Trojan", "history": []},
        }
    }


def test_group_snapshot_uses_only_requested_groups_and_sanitized_fields():
    with Controller(
        1234, "PRIVATE", httpx.MockTransport(lambda _: httpx.Response(200, json=proxy_data()))
    ) as api:
        groups = api.groups([GROUP, "missing"])
    assert groups == [
        {
            "name": GROUP,
            "type": "Selector",
            "current": NODE,
            "active": [NODE],
            "choices": {GROUP: NODE},
            "members": [
                {"name": NODE, "type": "VLESS", "delay": 120},
                {"name": "B", "type": "Trojan", "delay": None},
            ],
        }
    ]


def test_select_encodes_names_and_authenticates():
    requests = []

    def handle(request):
        assert request.headers["authorization"] == "Bearer PRIVATE"
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=proxy_data())
        assert b"%2F" in request.url.raw_path
        assert b"%3F%23" in request.url.raw_path
        assert request.url.query == b""
        assert request.content == b'{"name":"B"}'
        return httpx.Response(204)

    with Controller(1234, "PRIVATE", httpx.MockTransport(handle)) as api:
        api.select(GROUP, "B")
    assert [request.method for request in requests] == ["GET", "PUT"]


@pytest.mark.parametrize(
    "kind,member",
    [("Fallback", "B"), ("URLTest", "B"), ("LoadBalance", "B"), ("Selector", "unknown")],
)
def test_reject_invalid_selection_without_writing(kind, member):
    def handle(request):
        assert request.method == "GET"
        return httpx.Response(200, json=proxy_data(kind))

    with Controller(1234, "PRIVATE", httpx.MockTransport(handle)) as api:
        with pytest.raises(ClientError):
            api.select(GROUP, member)


def test_delay_is_per_node_and_does_not_reset_automatic_groups():
    def handle(request):
        assert request.method == "GET"
        if request.url.path == "/proxies":
            return httpx.Response(200, json=proxy_data("Fallback"))
        assert request.url.path.endswith("/delay")
        assert request.url.params["url"] == TEST_URL
        assert request.url.params["timeout"] == "5000"
        assert request.url.params["expected"] == "204"
        return httpx.Response(200, json={"delay": 42})

    with Controller(1234, "PRIVATE", httpx.MockTransport(handle)) as api:
        assert api.delay(NODE) == 42


def test_positive_delay_with_failed_expected_status_is_not_a_healthy_route():
    def handle(request):
        if request.url.path == "/proxies":
            data = proxy_data()
            data["proxies"][NODE]["extra"] = {TEST_URL: {"alive": False}}
            return httpx.Response(200, json=data)
        return httpx.Response(200, json={"delay": 42})

    with Controller(1234, "PRIVATE", httpx.MockTransport(handle)) as api:
        with pytest.raises(ClientError):
            api.delay(NODE)


@pytest.mark.parametrize(
    "status,body",
    [(504, {}), (200, {"delay": 0}), (200, {"delay": "100"}), (500, {"error": "PRIVATE"})],
)
def test_failed_delay_does_not_leak_response(status, body):
    def handle(request):
        return (
            httpx.Response(200, json=proxy_data())
            if request.url.path == "/proxies"
            else httpx.Response(status, json=body)
        )

    with Controller(1234, "PRIVATE", httpx.MockTransport(handle)) as api:
        with pytest.raises(ClientError) as error:
            api.delay(NODE)
    assert "PRIVATE" not in str(error.value)


def test_broken_api_response_is_recoverable():
    with Controller(
        1234, "PRIVATE", httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
    ) as api:
        with pytest.raises(ClientError):
            api.proxies()


def test_profile_without_groups_can_still_test_nodes():
    with Controller(
        1234, "PRIVATE", httpx.MockTransport(lambda _: httpx.Response(200, json=proxy_data()))
    ) as api:
        groups = api.groups([], [NODE, "B"])
    assert groups[0]["type"] == "Overview"
    assert [node["name"] for node in groups[0]["members"]] == [NODE, "B"]
