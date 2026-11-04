from nodera.connection_trace import ConnectionTrace


def test_first_response_and_disappearance_are_distinct_from_no_response():
    trace = ConnectionTrace()
    item = {
        "id": "connection-1",
        "metadata": {
            "network": "tcp",
            "host": "example.test",
            "destinationPort": "443",
            "processPath": "C:\\Apps\\browser.exe",
        },
        "upload": 500,
        "download": 0,
        "chains": ["Gateway"],
    }
    opened = trace.update([item])
    assert len(opened) == 1 and "connection_seen" in opened[0]
    assert "browser.exe" in opened[0] and "example.test:443" in opened[0]
    assert trace.update([item]) == []
    response = trace.update([{**item, "download": 1024}])
    assert len(response) == 1 and "response_observed" in response[0]
    ended = trace.update([])
    assert len(ended) == 1 and "connection_not_observed" in ended[0]
    assert "last_download=1024" in ended[0]


def test_stop_logs_reason_and_final_observed_counters():
    trace = ConnectionTrace()
    trace.update([{"id": "one", "upload": 12, "download": 0}])
    events = trace.clear("user disconnect")
    assert len(events) == 1 and "user disconnect" in events[0]
    assert "last_download=0" in events[0]
    assert trace.clear("again") == []
