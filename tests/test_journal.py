import json

from nodera.journal import Journal, compact_entries, profile_secrets, useful_event
from nodera.models import Profile


def test_brief_logs_keep_actions_and_errors_and_fold_repeats():
    def event(message, source="Nodera", level="info"):
        return dict(message=message, source=source, level=level, time="2026-11-04T10:00:00")

    assert useful_event(event("Подписка обновлена"))
    assert useful_event(event("DNS timeout", "Mihomo", "warning"))
    assert not useful_event(event("event=connection_seen", "Соединения"))
    assert not useful_event(event("event=subscription_response", "Подписки"))
    assert not useful_event(event("details", level="debug"))
    repeated = event("DNS timeout", "Mihomo", "warning")
    compact = compact_entries([repeated, repeated, repeated])
    assert len(compact) == 1
    assert compact[0]["message"].endswith("(повторено 3 раз)")


def test_credentials_are_redacted_before_memory_and_disk(tmp_path):
    profile = Profile(
        "P",
        "https://vpn.example/s/private-subscription",
        {
            "proxies": [{"password": "hidden-password", "uuid": "hidden-user-id"}],
        },
        "",
    )
    journal = Journal(tmp_path, limit=2)
    message = f"{profile.url} hidden-password hidden-user-id Bearer abc123 token=othersecret https://u:p@other.example/private?q=x"
    journal.add(message, secrets=profile_secrets(profile))
    text = journal.path.read_text(encoding="utf-8")
    for secret in (
        "private-subscription",
        "hidden-password",
        "hidden-user-id",
        "abc123",
        "othersecret",
        "u:p",
        "private?q",
    ):
        assert secret not in text
        assert secret not in journal.entries[0]["message"]
    assert len(Journal(tmp_path).entries) == 1
    journal.add("second")
    journal.add("third")
    assert [entry["message"] for entry in journal.entries] == ["second", "third"]
    journal.configure(2, False)
    journal.add("memory only")
    assert "memory only" not in journal.path.read_text(encoding="utf-8")
    journal.clear()
    assert not journal.entries and not journal.path.exists()


def test_rotation_keeps_current_and_three_backups(tmp_path):
    journal = Journal(tmp_path)
    journal.root.mkdir()
    for index in range(5):
        journal.path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        journal.add(f"event {index}")
    assert sorted(path.name for path in journal.root.iterdir()) == [
        "events.1.jsonl",
        "events.2.jsonl",
        "events.3.jsonl",
        "events.jsonl",
    ]
    assert json.loads(journal.path.read_text(encoding="utf-8"))["message"] == "event 4"


def test_corrupt_entries_do_not_prevent_startup(tmp_path):
    journal = Journal(tmp_path)
    journal.root.mkdir()
    journal.path.write_text(
        'invalid json\n{"time":1,"level":2,"source":3,"message":4}\n', encoding="utf-8"
    )
    assert not Journal(tmp_path).entries


def test_network_log_is_separate_rotated_and_redacted(tmp_path):
    journal = Journal(tmp_path, filename="network.jsonl", max_bytes=100)
    journal.add("event=connection_seen password=secret-value token=secret-token")
    journal.add("event=stop_requested reason=user disconnect")
    text = "".join(path.read_text(encoding="utf-8") for path in journal.root.glob("network*.jsonl"))
    assert "secret-value" not in text and "secret-token" not in text
    assert "stop_requested" in text
    assert not (journal.root / "events.jsonl").exists()
    assert (journal.root / "network.1.jsonl").exists()


def test_parallel_network_writers_do_not_corrupt_json_lines(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    journal = Journal(tmp_path, filename="network.jsonl")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda number: journal.add(f"event={number}"), range(100)))
    values = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    assert len(values) == 100
    assert len({item["message"] for item in values}) == 100


def test_short_credentials_and_wireguard_preshared_key_are_hidden(tmp_path):
    profile = Profile(
        "P",
        "",
        {
            "proxies": [
                {"password": "pin42", "preshared-key": "private-wg-value", "username": "usr"}
            ]
        },
        "",
    )
    journal = Journal(tmp_path)
    journal.add(
        "pin42 private-wg-value username=usr auth=temporary-auth", secrets=profile_secrets(profile)
    )
    text = journal.path.read_text(encoding="utf-8")
    assert all(
        secret not in text for secret in ("pin42", "private-wg-value", "temporary-auth", "=usr")
    )
