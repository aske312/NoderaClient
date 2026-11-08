"""Local PRO command lifecycle for demonstration profiles only."""

import copy
import json
import time
from dataclasses import asdict, replace
from uuid import uuid4

from nodera.channel_policy import ChannelPolicy, channel_capabilities
from nodera.channel_selection import SelectionResult, check_cancel, config_digest, selected_config
from nodera.models import ClientError, Profile, visible_proxies
from nodera.storage import atomic_write


class DemoChannelService:
    def __init__(self, root, scenarios=None):
        self.path = root / "demo-channel-selection.json"
        self.scenarios = scenarios

    def select_channels(
        self,
        profile,
        device_id,
        root,
        *,
        duration=60,
        require_udp=False,
        policy=None,
        cancel=None,
        progress=lambda _: None,
        **kwargs,
    ):
        from threading import Event

        from nodera.demo import demo_library

        cancel = cancel or Event()
        policy = (policy or ChannelPolicy()).validate()
        source = next(p for p in demo_library().profiles if p.id == profile.id)
        if self.scenarios:
            self.scenarios.check_subscription(profile.id, updating=True)
        rows, accepted = [], []
        for index, node in enumerate(visible_proxies(source.config)):
            check_cancel(cancel)
            if (
                not policy.matches(node)
                or policy.max_channels
                and len(accepted) >= policy.max_channels
            ):
                rows.append({"name": node["name"], "state": "skipped"})
                continue
            progress("Демо · проверка " + node["name"])
            cancel.wait(0.15)
            check_cancel(cancel)
            condition = (
                self.scenarios.condition(profile.id, node["name"], index)
                if self.scenarios
                else {"delay": 50, "udp": True}
            )
            if not condition["delay"] or require_udp and not condition["udp"]:
                rows.append({"name": node["name"], "state": "failed"})
                continue
            rows.append(
                {
                    "name": node["name"],
                    "state": "passed",
                    "seconds": duration,
                    "transfers": 3,
                    "bytes": 0,
                    "udp": condition["udp"],
                }
            )
            accepted.append(node["name"])
        if not accepted:
            raise ClientError("Нет демонстрационных каналов с выбранными параметрами.")
        return SelectionResult(
            replace(
                profile,
                channel_capabilities=channel_capabilities(source.config),
                config=selected_config(source.config, accepted),
                selections={},
                forced_server="",
            ),
            rows,
            "demo",
            time.time(),
            require_udp,
            duration,
            config_digest(source.config),
            previous_profile=copy.deepcopy(profile),
        )

    def _save(self, result):
        atomic_write(self.path, json.dumps(asdict(result), ensure_ascii=False).encode())
        return result

    def change_server_selection(self, result, device_id, action=None, **kwargs):
        result = copy.deepcopy(result)
        if action is None:
            result.operation_id = uuid4().hex
            result.server_state = "pending"
        elif action == "accept":
            result.server_state = "accepted"
        elif action in {"reject", "rollback"}:
            result.server_state = "rejected" if action == "reject" else "rolled_back"
            result.restored_config = copy.deepcopy(result.previous_profile.config)
        else:
            raise ClientError("Неизвестная демонстрационная команда.")
        return self._save(result)

    def discover_server_selection(self, profile, device_id, **kwargs):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key in ("profile", "previous_profile"):
                if data.get(key):
                    data[key] = Profile(**data[key])
            result = SelectionResult(**data)
            if result.profile.id != profile.id:
                raise ValueError
            result.local_applied = result.server_state == "accepted"
            return result
        except (OSError, ValueError, TypeError):
            raise ClientError("Демонстрационная версия для отката ещё не сохранена.") from None

    def read_server_selection(self, result, device_id, **kwargs):
        return self.discover_server_selection(result.profile, device_id)

    def apply_selection(self, result, previous, library, store, core, connected):
        updated = library.upsert(result.profile, activate=False)
        groups = core.apply_profile(result.profile) if connected else None
        store.save_library(updated)
        return updated, groups
