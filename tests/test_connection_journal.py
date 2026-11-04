import json

from nodera.connection_journal import ConnectionJournal
from tests.test_flow_monitor import flow


def test_separate_log_records_route_periodic_bytes_and_final_observation(tmp_path):
    journal = ConnectionJournal(tmp_path)
    journal.configure(True, 4, 15)
    journal.update([flow(upload=10, download=100)], now=1)
    journal.update([flow(upload=20, download=300)], now=10)
    journal.update([flow(upload=30, download=500)], now=16)
    journal.update([flow(upload=35, download=550)], now=20)
    journal.update([], now=21)
    records = [
        json.loads(line) for line in journal.journal.path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event"] for row in records] == ["opened", "traffic", "closed"]
    assert all(row["route"] == "Auto → Node" for row in records)
    assert records[-1]["download"] == 550 and records[-1]["upload"] == 35
    assert sum(row["delta_download"] for row in records) == 550
    assert sum(row["delta_upload"] for row in records) == 35
    assert all(row["last_observed"] for row in records)
    assert journal.journal.path.name == "connections.jsonl"


def test_connection_log_switch_is_independent_and_secrets_are_redacted(tmp_path):
    journal = ConnectionJournal(tmp_path)
    journal.configure(False)
    journal.update([flow()], now=1)
    assert not journal.journal.path.exists()
    journal.configure(True)
    sensitive = flow(upload=30, download=200)
    sensitive["chains"] = ["secret-node"]
    journal.update([sensitive], now=20, secrets=("secret-node",))
    journal.close("user disconnect", secrets=("secret-node",))
    text = journal.journal.path.read_text(encoding="utf-8")
    assert "secret-node" not in text
    assert "user disconnect" in text
    assert not journal.active
