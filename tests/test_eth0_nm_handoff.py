"""eth0 handoff NetworkManager -> systemd-networkd (_reconcile_eth0_nm).

Regression: NM (netplan-eth0) and networkd (40-eth0.network) both ran DHCP on
eth0, so the address flapped between two leases. Taking NM off eth0 without
systemd-resolved killed DNS (NM was the only DNS writer). The handoff must only
happen when 40-eth0.network exists AND systemd-resolved is active, and must
remove only the eth0 netplan file. Fixtures are the real netplan files NM
generated on node 42 (wlan0 PSK replaced).
"""

import nucleusd.apply as apply

NETPLAN_ETH0 = """network:
  version: 2
  ethernets:
    eth0:
      renderer: NetworkManager
      match: {}
      dhcp4: true
      dhcp6: true
      networkmanager:
        uuid: "75a1216a-9d1a-30cd-8aca-ace5526ec021"
        name: "netplan-eth0"
        passthrough:
          proxy._: ""
"""

NETPLAN_WLAN0 = """network:
  version: 2
  wifis:
    wlan0:
      renderer: NetworkManager
      match: {}
      dhcp4: true
      access-points:
        "natak":
          auth:
            key-management: "psk"
            password: "0000000000000000000000000000000000000000000000000000000000000000"
          networkmanager:
            uuid: "d5e10790-04ea-3d74-85e7-bd50f8b36df0"
            name: "netplan-wlan0-natak"
            passthrough:
              proxy._: ""
      networkmanager:
        uuid: "d5e10790-04ea-3d74-85e7-bd50f8b36df0"
        name: "netplan-wlan0-natak"
"""

ETH0_FILE = "90-NM-75a1216a-9d1a-30cd-8aca-ace5526ec021.yaml"
WLAN0_FILE = "90-NM-d5e10790-04ea-3d74-85e7-bd50f8b36df0.yaml"


class _Done:
    def __init__(self, returncode=0):
        self.returncode = returncode


def _setup(tmp_path, monkeypatch, eth0_network=True, resolved_rc=0):
    netplan = tmp_path / "netplan"
    netplan.mkdir()
    (netplan / ETH0_FILE).write_text(NETPLAN_ETH0)
    (netplan / WLAN0_FILE).write_text(NETPLAN_WLAN0)
    eth0_net = tmp_path / "40-eth0.network"
    if eth0_network:
        eth0_net.write_text("[Match]\nName=eth0\n\n[Network]\nDHCP=ipv4\nIPv4Forwarding=yes\n")
    conf = tmp_path / "conf.d" / "nucleus-eth0.conf"
    monkeypatch.setattr(apply, "ETH0_NETWORK", eth0_net)
    monkeypatch.setattr(apply, "NM_ETH0_CONF", conf)
    monkeypatch.setattr(apply, "NETPLAN_DIR", netplan)

    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["systemctl", "is-active"]:
            return _Done(resolved_rc)
        return _Done(0)

    monkeypatch.setattr(apply.subprocess, "run", run)
    return netplan, conf, calls


HANDOFF_CMDS = [
    ["nmcli", "general", "reload", "conf"],
    ["nmcli", "device", "set", "eth0", "managed", "no"],
    ["networkctl", "reconfigure", "eth0"],
]


def test_handoff_writes_conf_and_removes_only_eth0_netplan(tmp_path, monkeypatch):
    netplan, conf, calls = _setup(tmp_path, monkeypatch)
    changed = apply._reconcile_eth0_nm()
    assert conf.read_text() == (
        "[device-nucleus-eth0]\n"
        "match-device=interface-name:eth0\n"
        "managed=0\n"
    )
    assert not (netplan / ETH0_FILE).exists()
    assert (netplan / WLAN0_FILE).read_text() == NETPLAN_WLAN0
    assert changed == [str(conf), str(netplan / ETH0_FILE)]
    assert calls[-3:] == HANDOFF_CMDS


def test_handoff_idempotent(tmp_path, monkeypatch):
    _, _, calls = _setup(tmp_path, monkeypatch)
    apply._reconcile_eth0_nm()
    calls.clear()
    assert apply._reconcile_eth0_nm() == []
    assert not any(c in calls for c in HANDOFF_CMDS)


def test_no_handoff_when_resolved_inactive(tmp_path, monkeypatch):
    # Without systemd-resolved, NM is the only DNS writer — must keep eth0.
    netplan, conf, calls = _setup(tmp_path, monkeypatch, resolved_rc=3)
    assert apply._reconcile_eth0_nm() == []
    assert not conf.exists()
    assert (netplan / ETH0_FILE).exists()
    assert not any(c in calls for c in HANDOFF_CMDS)


def test_no_handoff_without_eth0_network(tmp_path, monkeypatch):
    # Fresh node before apply rendered 40-eth0.network: NM must keep eth0.
    netplan, conf, calls = _setup(tmp_path, monkeypatch, eth0_network=False)
    assert apply._reconcile_eth0_nm() == []
    assert not conf.exists()
    assert (netplan / ETH0_FILE).exists()
    assert calls == []
