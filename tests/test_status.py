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
    "add route 559159ce80 prefix 10.20.46.0/24 installed yes if wlan1\n"
    "ok\n"
)


def test_babel_neighbours_parsed(monkeypatch):
    class FakeSock:
        def __init__(self):
            self._buf = BABEL_DUMP.encode()
        def settimeout(self, *_): pass
        def sendall(self, *_): pass
        def recv(self, n):
            chunk, self._buf = self._buf[:n], self._buf[n:]
            return chunk
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(status.socket, "create_connection", lambda *a, **k: FakeSock())
    nbrs = status.babel_neighbours()
    assert len(nbrs) == 1
    n = nbrs[0]
    assert n["id"] == "559159c770"
    assert n["address"] == "fe80::a3c3:4e41:d850:bf66"
    assert n["if"] == "wlan1"
    assert n["cost"] == "258"


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
