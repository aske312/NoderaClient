import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from nodera.locations import RESOURCE
from nodera.models import Profile
from nodera.profiles_ui import subscription_source
from nodera.server_assets import node_countries, protocol_pixmap


@pytest.mark.parametrize(
    "url,source",
    [
        ("https://312node.net/sub/t", "PRO"),
        ("https://sub.312node.net/sub/t", "PRO"),
        ("https://312NODE.NET./sub/t", "PRO"),
        ("https://other312node.net/sub/t", "Mihomo"),
        ("https://312node.net.example/sub/t", "Mihomo"),
        ("https://312node.net@other.example/sub/t", "Mihomo"),
    ],
)
def test_subscription_source_uses_hostname_boundary(url, source):
    assert subscription_source(url) == source


def test_protocol_icons_and_all_bundled_flags_render():
    app = QApplication.instance() or QApplication([])
    for protocol in (
        "vless",
        "hysteria2",
        "tuic",
        "ss",
        "trojan",
        "wireguard",
        "amneziawg",
        "unknown",
    ):
        assert not protocol_pixmap(protocol).isNull()
    flags = list((RESOURCE / "flags").glob("*.svg"))
    assert len(flags) >= 250
    assert all(QSvgRenderer(str(path)).isValid() for path in flags)
    assert app is not None


def test_all_route_and_verified_countries_are_preserved():
    name = "Germany → Finland"
    profile = Profile("Test", "", {"proxies": [{"name": name, "type": "vless"}]}, "")
    codes = node_countries(profile, name, {"rows": [{"country": "SE"}, {"country": "FI"}]})
    assert codes == ["DE", "FI", "SE"]


def test_manual_route_overrides_name_hints():
    profile = Profile(
        "Test",
        "",
        {"proxies": [{"name": "Germany", "type": "vless"}]},
        "",
        route_overrides={"Germany": [{"role": "server", "country": "NL"}]},
    )
    assert node_countries(profile, "Germany") == ["NL"]


def test_flag_without_map_coordinates_is_still_shown_on_server():
    name = "🇽🇰 Test"
    profile = Profile("Test", "", {"proxies": [{"name": name, "type": "ss"}]}, "")
    assert "XK" in node_countries(profile, name)


def test_unknown_countries_use_planet_alongside_known_relay_flag():
    name = "VLESS · Relay: Gremany"
    profile = Profile("Test", "", {"proxies": [{"name": name, "type": "vless"}]}, "")
    assert node_countries(profile, name) == ["DE", "XX"]
