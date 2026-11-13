"""<serial>.local + <serial>-nucleus.local reach the UI, incl. with no internet.

Regression: br-lan DHCP handed out br_lan.dns (8.8.8.8), so phones that also
send .local lookups to unicast DNS hung with no uplink. Clients now get the
node as DNS (nucleus-dns), which answers the node's own .local names locally.
"""

from nucleusd import apply
from nucleusd.apply import render_all
from nucleusd.schema import NucleusConfig

DNSMASQ = "/etc/nucleus/dnsmasq-nucleus.conf"
ALIAS = "/etc/nucleus/mdns-alias.env"
BRLAN = "/etc/systemd/network/21-br-lan.network"
NGINX = "/etc/nginx/sites-available/nucleus"


def cfg(node_id=42, **extra):
    return NucleusConfig.model_validate({
        "node": {"id": node_id},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        **extra,
    })


def test_local_names_from_serial():
    assert cfg(42).node.local_names == ["0042.local", "0042-nucleus.local"]
    assert cfg(7).node.local_names == ["0007.local", "0007-nucleus.local"]


def test_dnsmasq_answers_own_names_and_forwards_rest():
    d = render_all(cfg(42))[DNSMASQ]
    assert "host-record=0042.local,0042-nucleus.local,10.20.42.1\n" in d
    assert "server=8.8.8.8\n" in d
    assert "interface=br-lan\n" in d
    assert "no-dhcp-interface=br-lan\n" in d
    assert "no-resolv\n" in d


def test_dnsmasq_names_are_per_node():
    d = render_all(cfg(7))[DNSMASQ]
    assert "host-record=0007.local,0007-nucleus.local,10.20.7.1\n" in d
    assert "0042" not in d


def test_dnsmasq_upstream_is_br_lan_dns():
    d = render_all(cfg(42, br_lan={"dns": "1.1.1.1"}))[DNSMASQ]
    assert "server=1.1.1.1\n" in d


def test_dhcp_hands_out_node_as_dns():
    n = render_all(cfg(42))[BRLAN]
    assert "DNS=10.20.42.1\n" in n
    assert "DNS=8.8.8.8" not in n


def test_mdns_alias_env():
    e = render_all(cfg(42))[ALIAS]
    assert "NUCLEUS_MDNS_NAME=0042.local\n" in e
    assert "NUCLEUS_MDNS_ADDR=10.20.42.1\n" in e


def test_nginx_serves_both_names():
    n = render_all(cfg(42))[NGINX]
    assert n.count("server_name 0042.local 0042-nucleus.local _;") == 2


def test_cert_sans_cover_both_names(monkeypatch):
    monkeypatch.setattr(apply, "_global_ipv4s", lambda: ["10.20.42.1"])
    assert apply._wanted_sans(cfg(42).node.local_names) == [
        "DNS:0042.local", "DNS:0042-nucleus.local", "IP:10.20.42.1"]


def test_reconcile_restarts_only_changed_unit(monkeypatch):
    calls = []
    monkeypatch.setattr(apply.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    out = apply._reconcile_local_names([str(apply.DNSMASQ_CONF)])
    assert out == ["nucleus-dns"]
    assert ["systemctl", "restart", "nucleus-dns.service"] in calls
    assert ["systemctl", "enable", "--now", "nucleus-mdns-alias.service"] in calls
    assert ["systemctl", "restart", "nucleus-mdns-alias.service"] not in calls
