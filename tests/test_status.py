"""status.iface_addrs parser test — the wlan0-absent regression."""

import subprocess

from nucleusd import status

LINK = (
    "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP mode DEFAULT group default qlen 1000\n"
    "3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel master br-lan state UP mode DEFAULT group default qlen 1000\n"
    "4: wlan1: <NO-CARRIER,BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state DORMANT mode DEFAULT group default qlen 1000\n"
    "5: br-lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP mode DEFAULT group default qlen 1000\n"
)
ADDR = (
    "2: eth0    inet 10.0.0.18/24 brd 10.0.0.255 scope global dynamic eth0\\ valid_lft forever\n"
    "4: wlan1    inet 10.20.1.42/24 brd 10.20.1.255 scope global wlan1\\ valid_lft forever\n"
    "5: br-lan    inet 10.20.42.1/24 brd 10.20.42.255 scope global br-lan\\ valid_lft forever\n"
)


def _fake_run(cmd, **kw):
    text = LINK if "link" in cmd else ADDR
    return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")


BABEL_DUMP = (
    "BABEL 1.0\n"
    "version babeld-1.13.1\n"
    "host 0042-nucleus\n"
    "my-id 2e:cf:67:ff:fe:6d:8b:b6\n"
    "ok\n"
    "add interface wlan1 up true ipv6 fe80::b2 ipv4 10.20.1.42\n"
    "add neighbour 559159c770 address fe80::a3c3:4e41:d850:bf66 if wlan1 "
    "reach feff ureach 0000 rxcost 258 txcost 256 cost 258\n"
    "add route 559159ce80 prefix 10.20.46.0/24 from ::/0 installed yes "
    "id 00:00:00:00:00:00:00:46 metric 258 refmetric 0 "
    "via fe80::a3c3:4e41:d850:bf66 if wlan1\n"
    "ok\n"
)


# Two directly-connected neighbours. Each originates its own br-lan /24
# (refmetric 0) AND re-advertises the *other* node's /24 as a relayed route
# (refmetric > 0) with its own link-local as the via. The relayed lines must be
# ignored so each neighbour resolves to its own distinct mesh IPv4 — the
# "same IP for both connected nodes" regression.
BABEL_DUMP_TWO = (
    "BABEL 1.0\n"
    "version babeld-1.13.1\n"
    "host 0042-nucleus\n"
    "my-id 2e:cf:67:ff:fe:6d:8b:b6\n"
    "ok\n"
    "add interface wlan1 up true ipv6 fe80::b2 ipv4 10.20.1.42\n"
    "add neighbour AAAAAAAAAA address fe80::aaaa if wlan1 "
    "reach ffff ureach 0000 rxcost 256 txcost 256 cost 256\n"
    "add neighbour BBBBBBBBBB address fe80::bbbb if wlan1 "
    "reach ffff ureach 0000 rxcost 256 txcost 256 cost 256\n"
    # A originates its own /24
    "add route r1 prefix 10.20.5.0/24 from ::/0 installed yes "
    "id 00:00:00:00:00:00:00:05 metric 256 refmetric 0 via fe80::aaaa if wlan1\n"
    # B originates its own /24
    "add route r2 prefix 10.20.7.0/24 from ::/0 installed yes "
    "id 00:00:00:00:00:00:00:07 metric 256 refmetric 0 via fe80::bbbb if wlan1\n"
    # A relays B's /24 (refmetric > 0) — must NOT map fe80::aaaa to 10.20.1.7
    "add route r3 prefix 10.20.7.0/24 from ::/0 installed no "
    "id 00:00:00:00:00:00:00:07 metric 512 refmetric 256 via fe80::aaaa if wlan1\n"
    # B relays A's /24 (refmetric > 0) — must NOT map fe80::bbbb to 10.20.1.5
    "add route r4 prefix 10.20.5.0/24 from ::/0 installed no "
    "id 00:00:00:00:00:00:00:05 metric 512 refmetric 256 via fe80::bbbb if wlan1\n"
    "ok\n"
)


def _fake_sock_factory(dump):
    class FakeSock:
        def __init__(self):
            self._buf = dump.encode()
        def settimeout(self, *_): pass
        def sendall(self, *_): pass
        def recv(self, n):
            chunk, self._buf = self._buf[:n], self._buf[n:]
            return chunk
        def __enter__(self): return self
        def __exit__(self, *a): pass
    return FakeSock


def test_babel_neighbours_parsed(monkeypatch):
    monkeypatch.setattr(status.socket, "create_connection",
                        lambda *a, **k: _fake_sock_factory(BABEL_DUMP)())
    nbrs = status.babel_neighbours()
    assert len(nbrs) == 1
    n = nbrs[0]
    assert n["id"] == "559159c770"
    assert n["address"] == "fe80::a3c3:4e41:d850:bf66"
    assert n["if"] == "wlan1"
    assert n["cost"] == "258"
    assert n["ipv4"] == "10.20.1.46"


# Whole-mesh route table: node 5 is a direct neighbour (refmetric 0), node 9 is
# multi-hop reached via node 5 (refmetric > 0). Only installed=yes routes count;
# the backup route to node 9 (installed no) and self/gateway prefixes are ignored.
BABEL_DUMP_ROUTES = (
    "BABEL 1.0\n"
    "version babeld-1.13.1\n"
    "host 0042-nucleus\n"
    "my-id 2e:cf:67:ff:fe:6d:8b:b6\n"
    "ok\n"
    "add interface wlan1 up true ipv6 fe80::b2 ipv4 10.20.1.42\n"
    "add neighbour AAAAAAAAAA address fe80::aaaa if wlan1 "
    "reach ffff ureach 0000 rxcost 256 txcost 256 cost 256\n"
    # node 5: direct (refmetric 0), installed
    "add route r1 prefix 10.20.5.0/24 from ::/0 installed yes "
    "id 00:00:00:00:00:00:00:05 metric 256 refmetric 0 via fe80::aaaa if wlan1\n"
    # node 5 also originates so via_to_ipv4[fe80::aaaa] = 10.20.1.5
    # node 9: multi-hop via node 5 (refmetric > 0), installed
    "add route r2 prefix 10.20.9.0/24 from ::/0 installed yes "
    "id 00:00:00:00:00:00:00:09 metric 512 refmetric 256 via fe80::aaaa if wlan1\n"
    # node 9 backup route: installed no -> ignored
    "add route r3 prefix 10.20.9.0/24 from ::/0 installed no "
    "id 00:00:00:00:00:00:00:09 metric 999 refmetric 700 via fe80::aaaa if wlan1\n"
    # gateway/self prefixes -> ignored (0.0.0.0/0, 10.20.1.0/24)
    "add route r4 prefix 0.0.0.0/0 from ::/0 installed yes "
    "id 00:00:00:00:00:00:00:05 metric 256 refmetric 0 via fe80::aaaa if wlan1\n"
    "add route r5 prefix 10.20.1.0/24 from ::/0 installed yes "
    "id 00:00:00:00:00:00:00:05 metric 256 refmetric 0 via fe80::aaaa if wlan1\n"
    "ok\n"
)


def test_babel_routes_direct_and_multihop(monkeypatch):
    """Whole-mesh view: direct vs multi-hop nodes, backups/self excluded."""
    monkeypatch.setattr(status.socket, "create_connection",
                        lambda *a, **k: _fake_sock_factory(BABEL_DUMP_ROUTES)())
    routes = status.babel_routes()
    by_node = {r["node"]: r for r in routes}
    assert set(by_node) == {"10.20.1.5", "10.20.1.9"}
    assert by_node["10.20.1.5"]["direct"] is True
    assert by_node["10.20.1.5"]["via"] is None
    assert by_node["10.20.1.5"]["metric"] == 256
    assert by_node["10.20.1.9"]["direct"] is False
    assert by_node["10.20.1.9"]["via"] == "10.20.1.5"
    assert by_node["10.20.1.9"]["metric"] == 512


def test_two_neighbours_distinct_ipv4(monkeypatch):
    """Regression: relayed routes must not collapse two neighbours to one IPv4."""
    monkeypatch.setattr(status.socket, "create_connection",
                        lambda *a, **k: _fake_sock_factory(BABEL_DUMP_TWO)())
    nbrs = status.babel_neighbours()
    assert len(nbrs) == 2
    by_addr = {n["address"]: n["ipv4"] for n in nbrs}
    assert by_addr["fe80::aaaa"] == "10.20.1.5"
    assert by_addr["fe80::bbbb"] == "10.20.1.7"
    # Each connected node shows a *distinct* mesh IP.
    assert by_addr["fe80::aaaa"] != by_addr["fe80::bbbb"]


def test_bridge_member_not_absent(monkeypatch):
    monkeypatch.setattr(status.subprocess, "run", _fake_run)
    out = status.iface_addrs()
    # wlan0 has no IP (bridge member) but must be reported present, not absent.
    assert out["wlan0"]["state"] == "present"
    assert out["wlan0"]["master"] == "br-lan"
    assert out["wlan0"]["addrs"] == []
    # addressed interfaces still report their addrs
    assert "10.20.1.42/24" in out["wlan1"]["addrs"]
    assert out["wlan1"]["oper_state"] == "DORMANT"
    assert out["br-lan"]["oper_state"] == "UP"
