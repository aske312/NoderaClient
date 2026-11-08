"""Narrow, credential-safe adapter for the local Mihomo REST API.

Contract: https://wiki.metacubex.one/api/#proxies
"""

from urllib.parse import quote

import httpx

from nodera.models import ClientError
from nodera.route_health import PROBE_PREFIX

TEST_URL = "https://www.gstatic.com/generate_204"


def active_nodes(proxies, name, visited=None):
    visited = set(visited or ())
    if name in visited or len(visited) > 32:
        return []
    visited.add(name)
    node = proxies.get(name)
    if not isinstance(node, dict) or node.get("type") in {"Direct", "Reject", "Pass", "Overview"}:
        return []
    if isinstance(node.get("all"), list):
        current = node.get("now")
        if isinstance(current, str) and current in node["all"]:
            return active_nodes(proxies, current, visited)
        return []
    return [name]


class Controller:
    def __init__(self, port: int, secret: str, transport=None):
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=2,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def request(self, method: str, path: str, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None
        except (httpx.HTTPError, ValueError) as exc:
            raise ClientError("Нет ответа от API ядра. Проверьте состояние подключения.") from exc

    def proxies(self) -> dict:
        data = self.request("GET", "/proxies")
        if not isinstance(data, dict) or not isinstance(data.get("proxies"), dict):
            raise ClientError("Ядро вернуло некорректный список серверов.")
        return data["proxies"]

    def groups(self, names: list[str], nodes: list[str] | None = None) -> list[dict]:
        proxies = self.proxies()
        result = []
        if not names and nodes:
            names = ["Подключения профиля"]
            proxies = {**proxies, names[0]: {"type": "Overview", "all": nodes}}
        for name in names:
            group = proxies.get(name)
            if not isinstance(group, dict) or not isinstance(group.get("all"), list):
                continue
            members = []
            for member in group["all"]:
                if isinstance(member, str) and member.startswith(
                    (PROBE_PREFIX, "__312_transport_")
                ):
                    continue
                if not isinstance(member, str):
                    continue
                node = proxies.get(member, {})
                if not isinstance(node, dict):
                    node = {}
                history = node.get("history") or []
                delay = (
                    history[-1].get("delay")
                    if isinstance(history, list) and history and isinstance(history[-1], dict)
                    else None
                )
                members.append(
                    {
                        "name": member,
                        "type": node.get("type", "—"),
                        "delay": delay if type(delay) is int and delay >= 0 else None,
                    }
                )
            result.append(
                {
                    "name": name,
                    "type": group.get("type", ""),
                    "current": group.get("now", ""),
                    "active": active_nodes(proxies, name),
                    "choices": {
                        key: item["now"]
                        for key, item in proxies.items()
                        if isinstance(item, dict) and isinstance(item.get("now"), str)
                    },
                    "members": members,
                }
            )
        return result

    def select(self, group: str, member: str) -> None:
        proxies = self.proxies()
        item = proxies.get(group, {})
        # Automatic groups keep the subscription policy; no accidental pinning.
        if item.get("type") != "Selector":
            raise ClientError("Эта группа управляется автоматически по правилам подписки.")
        if member not in item.get("all", []):
            raise ClientError("Сервер больше не входит в группу. Обновите список.")
        self.request("PUT", f"/proxies/{quote(group, safe='')}", json={"name": member})

    def close_connection(self, identity):
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            raise ClientError("Некорректный идентификатор соединения.")
        self.request("DELETE", "/connections/" + quote(identity, safe=""))

    def force_server(self, member):
        # GLOBAL is Mihomo's manual selector, independent of subscription groups.
        self.select("GLOBAL", member)
        self.request("PATCH", "/configs", json={"mode": "global"})

    def recheck_routes(self, names, url=TEST_URL):
        proxies = self.proxies()
        checked = []
        failures = []
        pending, visited = list(names), set()
        while pending:
            name = pending.pop(0)
            if name in visited:
                continue
            visited.add(name)
            group = proxies.get(name, {})
            pending.extend(
                member
                for member in group.get("all", [])
                if member in proxies and isinstance(proxies[member].get("all"), list)
            )
            if proxies.get(name, {}).get("type") not in {"Fallback", "URLTest", "LoadBalance"}:
                continue
            try:
                self.request(
                    "GET",
                    "/group/" + quote(name, safe="") + "/delay",
                    params={
                        "url": group.get("testUrl") or url,
                        "timeout": 5000,
                        "expected": group.get("expectedStatus") or "204",
                    },
                    timeout=8,
                )
            except ClientError as exc:
                failures.append(exc)
                continue
            checked.append(name)
        if failures and not checked:
            raise failures[0]
        return checked

    def delay(self, name: str) -> int:
        proxies = self.proxies()
        if name not in proxies:
            raise ClientError("Сервер больше не доступен в текущем профиле.")
        try:
            response = self.client.get(
                f"/proxies/{quote(name, safe='')}/delay",
                params={"url": TEST_URL, "timeout": 5000, "expected": "204"},
                timeout=7,
            )
            if response.status_code in {408, 504}:
                raise ClientError("Сервер не прошёл HTTPS-проверку за 5 секунд.")
            response.raise_for_status()
            data = response.json()
            delay = data.get("delay") if isinstance(data, dict) else None
            if type(delay) is not int or delay <= 0:
                raise ClientError("Сервер не прошёл HTTPS-проверку.")
            state = (self.proxies().get(name, {}).get("extra") or {}).get(TEST_URL, {})
            if state.get("alive") is False:
                raise ClientError("Маршрут не вернул ожидаемый ответ проверки сети.")
            return delay
        except (httpx.HTTPError, ValueError) as exc:
            raise ClientError(
                "HTTPS-проверка не удалась. Проверьте сервер или выберите другой узел."
            ) from exc
