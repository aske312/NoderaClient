from nodera.core import runtime_config
from nodera.models import Profile, Settings
from nodera.profile_view import effective_tun, policy_view, quota_view, rule_rows


def test_unknown_limits_are_distinct_from_unlimited():
    unknown = quota_view(None, now=100)
    profile = Profile(
        "PRO", "", {}, "", usage={"upload": 0, "download": 0, "total": 0, "expire": 0}
    )
    unlimited = quota_view(profile, now=100)
    assert unknown["traffic"] == "Не передан"
    assert unknown["days"] == "Не указан"
    assert unlimited["traffic"] == "Без лимита"
    assert unlimited["days"] == "Без срока"


def test_quota_exhaustion_and_expiry_come_from_server_snapshot():
    profile = Profile(
        "PRO", "", {}, "", usage={"upload": 30, "download": 80, "total": 100, "expire": 99}
    )
    view = quota_view(profile, now=100)
    assert view["exhausted"] and view["expired"]
    assert view["traffic"] == "0 Б"
    assert view["days"] == "Истёк"
    profile.usage["expire"] = 86500
    assert quota_view(profile, now=100)["days"] == "1 дн."


def test_pro_rules_and_dns_are_reported_without_inventing_server_flags():
    profile = Profile(
        "PRO",
        "",
        {
            "rules": [
                "DOMAIN-SUFFIX,example.com,PRO",
                "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
                "AND,((NETWORK,TCP),(PROCESS-NAME,app.exe)),PRO",
                "DOMAIN,bad.example,REJECT",
                "MATCH,DIRECT",
            ],
            "proxy-groups": [{"name": "PRO", "type": "fallback"}],
            "proxies": [{"type": "vless"}, {"type": "hysteria2"}],
            "dns": {
                "enable": True,
                "respect-rules": True,
                "enhanced-mode": "fake-ip",
                "nameserver": ["https://private:password@dns.example/SECRET?token=PRIVATE"],
            },
        },
        "",
    )
    view = policy_view(profile)
    assert view["strategy"] == "Резервирование"
    assert view["routing"] == "Остальное — напрямую"
    assert view["blocked"] == 1
    assert view["processes"] == 1
    assert view["route_dns"] is True
    assert view["resolvers"] == ["dns.example"]
    assert "SECRET" not in str(view) and "password" not in str(view)
    assert rule_rows(profile.config)[1] == ("IP-CIDR", "10.0.0.0/8", "DIRECT")
    assert rule_rows(profile.config)[2] == ("AND", "((NETWORK,TCP),(PROCESS-NAME,app.exe))", "PRO")


def test_subscription_cannot_disable_local_vpn_mode():
    config = {"tun": {"enable": False}, "rules": ["MATCH,PRO"]}
    assert effective_tun(config, Settings(tun=True)) is True
    assert effective_tun({"tun": {"enable": True}}, Settings(tun=False)) is False
    generated = runtime_config(config, Settings(tun=True), 50000, "secret")
    assert generated["tun"]["enable"] is True
    assert generated["rules"] == config["rules"]
