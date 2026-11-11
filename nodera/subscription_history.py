"""Restore remote configuration while keeping current local policy and access state."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from nodera.models import ClientError, visible_proxies
from nodera.pro_provenance import profile_public_key


def restored_profile(current, previous):
    if current.id != previous.id or current.url != previous.url:
        raise ClientError("Предыдущая версия относится к другой подписке.")
    pinned = profile_public_key(current)
    if pinned and profile_public_key(previous) != pinned:
        raise ClientError(
            "Нельзя откатиться к версии с другим ключом подписки. Обновите подписку вручную."
        )
    names = {node.get("name") for node in visible_proxies(previous.config)}
    return replace(
        current,
        config=deepcopy(previous.config),
        pro_verification=deepcopy(previous.pro_verification),
        updated_at=datetime.now(timezone.utc).isoformat(),
        forced_server=current.forced_server if current.forced_server in names else "",
        auto_update_paused=True,
    )
