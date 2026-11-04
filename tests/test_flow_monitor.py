from nodera.flow_monitor import FlowMonitor, domain, grouped_flows


def flow(key="a", host="example.org", process="chrome.exe", upload=10, download=100):
    return {
        "id": key,
        "metadata": {
            "processPath": "C:\\Apps\\" + process,
            "host": host,
            "destinationIP": "1.1.1.1",
            "destinationPort": "443",
            "network": "tcp",
        },
        "upload": upload,
        "download": download,
        "chains": ["Node", "Auto"],
        "rule": "DomainSuffix",
    }


def test_flow_metadata_rates_expiry_and_url_credentials_are_not_retained():
    monitor = FlowMonitor()
    first = flow(host="https://user:password@example.org/private?token=secret")
    monitor.update([first], now=100)
    row = monitor.rows()[0]
    assert row["host"] == "example.org"
    assert row["process"] == "chrome.exe" and row["browser"]
    assert row["route"] == "Auto → Node"
    assert "password" not in str(row) and "secret" not in str(row)
    monitor.update([flow(upload=14, download=220)], now=102)
    assert monitor.rows()[0]["up_rate"] == 2
    assert monitor.rows()[0]["down_rate"] == 60
    monitor.update([], now=103)
    assert not monitor.rows()
    closed = monitor.rows(active_only=False)[0]
    assert not closed["active"] and closed["down_rate"] == 0
    monitor.update([], now=403)
    assert not monitor.rows(active_only=False)


def test_application_site_grouping_and_filters():
    assert domain("2606:4700:4700::1111") == "2606:4700:4700::1111"
    assert domain("https://[2606:4700:4700::1111]:443/path") == "2606:4700:4700::1111"
    monitor = FlowMonitor()
    monitor.update(
        [flow(), flow("b", "other.example"), flow("c", "example.org", "service.exe")], now=100
    )
    apps = grouped_flows(monitor.rows(), "process")
    chrome = next(item for item in apps if item["name"] == "chrome.exe")
    assert chrome["active"] == 2 and chrome["download"] == 200
    assert chrome["sites"] == {"example.org", "other.example"}
    assert len(grouped_flows(monitor.rows(), "site")) == 2
    assert len(monitor.rows(browsers_only=True)) == 2
    assert len(monitor.rows(site="example.org")) == 2
    assert len(monitor.rows(process="c:\\apps\\service.exe")) == 1
    assert len(monitor.rows(query="OTHER.EXAMPLE")) == 1


def test_malformed_duplicate_and_capped_observations(monkeypatch):
    monitor = FlowMonitor()
    monitor.update([None, {}, {"id": "bad", "metadata": []}, flow(), flow()], now=1)
    assert len(monitor.rows()) == 1
    monkeypatch.setattr("nodera.flow_monitor.MAX_FLOWS", 3)
    monitor.update([flow(str(index)) for index in range(5)], now=2)
    assert monitor.truncated and len(monitor.flows) == 3
    monitor.clear()
    assert not monitor.flows and not monitor.truncated
