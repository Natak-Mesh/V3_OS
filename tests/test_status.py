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
