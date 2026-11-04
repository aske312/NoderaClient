import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QDialog

from nodera.controller import active_nodes
from nodera.dialogs import RouteDialog
from nodera.locations import RESOURCE, active_locations, infer_country, map_data
from nodera.models import Profile, Settings
from nodera.storage import Store
from nodera.ui import MainWindow


@pytest.mark.parametrize(
    "name,code",
    [
        ("🇩🇪 Reality", "DE"),
        ("Frankfurt · Reality", "DE"),
        ("Хельсинки 02", "FI"),
        ("DE01 VLESS", "DE"),
        ("[NL] node", "NL"),
        ("Hong Kong", "HK"),
        ("Singapore", "SG"),
        ("USA #1", "US"),
        ("Германия", "DE"),
        ("Gremany", "DE"),
        ("Suomi", "FI"),
        ("Кишинёв", "MD"),
        ("Reykjavik Iceland", "IS"),
        ("Сейшелы", "SC"),
        ("Кыргызстан", "KG"),
        ("Belarus", "BY"),
        ("Cape Verde", "CV"),
        ("Curacao", "CW"),
        ("PRO · Основной канал", None),
        ("Reality", None),
        ("SS", None),
    ],
)
def test_offline_country_hints(name, code):
    result = infer_country({"name": name})
    assert (result["code"] if result else None) == code


def test_explicit_country_and_override_take_precedence():
    assert infer_country({"name": "🇩🇪 Test", "country": "FI"})["code"] == "FI"
    assert infer_country({"name": "A", "country": "Germany"})["code"] == "DE"
    assert infer_country({"name": "🇩🇪 Test"}, "NL")["code"] == "NL"


def test_controller_resolves_nested_hidden_groups_and_avoids_cycles():
    proxies = {
        "PRO": {"type": "Selector", "now": "Auto", "all": ["Auto"]},
        "Auto": {"type": "URLTest", "now": "🇫🇮 A", "all": ["🇫🇮 A"]},
        "🇫🇮 A": {"type": "VLESS"},
    }
    assert active_nodes(proxies, "PRO") == ["🇫🇮 A"]
    proxies["Auto"].update(now="PRO", all=["PRO"])
    assert active_nodes(proxies, "PRO") == []
    assert active_nodes({"DIRECT": {"type": "Direct"}}, "DIRECT") == []
    assert active_nodes({"Auto": {"type": "LoadBalance", "all": ["A"]}}, "Auto") == []


def test_only_live_resolved_nodes_get_markers():
    profile = Profile(
        "P", "", {"proxies": [{"name": "A", "type": "ss"}]}, "", locations={"A": "DE"}
    )
    groups = [{"name": "PRO", "current": "Auto", "active": ["A"]}]
    assert active_locations(profile, groups, False) == []
    assert [item["code"] for item in active_locations(profile, groups, True)] == ["DE"]
    assert active_locations(profile, [{"name": "PRO", "active": []}], True) == []
    direct = replace(profile, config={**profile.config, "mode": "direct"})
    assert active_locations(direct, groups, True) == []


def test_assets_cover_all_supported_countries():
    app = QApplication.instance() or QApplication([])
    assert len(map_data()["land"]) > 50
    for code in map_data()["countries"]:
        assert QSvgRenderer(str(RESOURCE / "flags" / f"{code.lower()}.svg")).isValid()
    app.processEvents()


def test_map_follows_switch_disconnect_and_saved_override(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "P",
        "",
        {
            "proxies": [{"name": "Frankfurt", "type": "ss"}],
            "proxy-groups": [{"name": "PRO", "type": "select", "proxies": ["Frankfurt"]}],
        },
        "",
    )
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), profile)
    window.timer.stop()
    assert window.world_map.locations == []
    window.connected = True
    window.set_groups(
        [
            {
                "name": "PRO",
                "type": "Selector",
                "current": "Frankfurt",
                "active": ["Frankfurt"],
                "members": [{"name": "Frankfurt", "type": "ss", "delay": None}],
            }
        ]
    )
    assert window.world_map.locations[0]["code"] == "DE"
    monkeypatch.setattr(RouteDialog, "exec", lambda *args: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(RouteDialog, "route", lambda *args: [{"role": "server", "country": "FI"}])
    window.choose_location()
    assert window.task.wait(5000)
    app.processEvents()
    assert window.task is None
    assert window.world_map.locations[0]["code"] == "FI"
    assert Store(tmp_path).load_profile().route_overrides == {
        "Frankfurt": [{"role": "server", "country": "FI"}]
    }
    window.set_groups(
        [{"name": "PRO", "type": "URLTest", "current": "Tokyo", "active": ["Tokyo"], "members": []}]
    )
    assert window.world_map.locations[0]["code"] == "JP"
    window.set_disconnected()
    assert window.world_map.locations == []
    window.close()
    app.processEvents()
