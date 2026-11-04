import ipaddress
import os

import pytest

from nodera.core import runtime_config
from nodera.models import ClientError, Settings
from nodera.tun_address import choose_tun_range, ipv4_assignments


def test_other_vpn_address_is_not_reused():
    selected = choose_tun_range(["198.18.0.1/30", "192.168.0.10/24"])
    assert selected == "198.19.0.1/16"


def test_both_default_ranges_occupied_selects_a_disjoint_pool():
    occupied = ["198.18.0.1/30", "198.19.0.1/30"]
    selected = ipaddress.ip_interface(choose_tun_range(occupied))
    assert all(
        not selected.network.overlaps(ipaddress.ip_interface(item).network) for item in occupied
    )


def test_exhausted_space_reports_address_conflict_not_disabled_tun():
    with pytest.raises(ClientError, match="диапазона"):
        choose_tun_range(["198.18.0.1/15"])


def test_runtime_dns_and_tun_use_local_address_pool():
    profile = {"dns": {"fake-ip-range": "198.18.0.1/16"}}
    config = runtime_config(profile, Settings(), 50000, "test", fake_ip_range="198.19.0.1/16")
    assert config["dns"]["fake-ip-range"] == "198.19.0.1/16"
    assert profile["dns"]["fake-ip-range"] == "198.18.0.1/16"


@pytest.mark.skipif(os.name != "nt", reason="Windows IP Helper")
def test_windows_assignments_are_readable_without_elevation():
    assignments = ipv4_assignments()
    assert assignments
    assert all(isinstance(item, ipaddress.IPv4Interface) for item in assignments)
