"""CoT bridge multicast -> LoRa source filter with eth0.mcast_mode: mesh_relay.

The filter only accepted this node's br-lan subnet, so ATAK CoT from an
RF-silent node on the eth0 wire (br-lan 10.20.<id>.0/24, arriving via smcroute)
was never sent to LoRa. Source IPs are real: 10.20.42.1 / 10.0.0.91 from node
0042's live `ip mroute`; 0047 is the fiber peer. Run off-box: `pytest`.
"""

import ipaddress
import sys
import types

if "pubsub" not in sys.modules:
    _pubsub = types.ModuleType("pubsub")
    _pubsub.pub = types.SimpleNamespace(
        subscribe=lambda *a, **k: None,
        sendMessage=lambda *a, **k: None,
        AUTO_TOPIC=object(),
    )
    sys.modules["pubsub"] = _pubsub

import pytest  # noqa: E402

from nucleusd.meshtastic import cot_bridge  # noqa: E402

_REQUIRED = ('node:\n  id: 42\nmesh:\n  password: "52235223"\n'
             'ap:\n  password: "52235223"\n')


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    monkeypatch.setattr(cot_bridge, "local_subnet", ipaddress.ip_network("10.20.42.0/24"))
    monkeypatch.setattr(cot_bridge, "relay_subnets", [])
    monkeypatch.setattr(cot_bridge, "relay_exclude_ips", set())


def _conf(tmp_path, monkeypatch, eth0):
    p = tmp_path / "config.yaml"
    p.write_text(_REQUIRED + "eth0:\n" + eth0)
    monkeypatch.setattr(cot_bridge, "MESH_CONF", str(p))
    return cot_bridge._read_mesh_conf()


def test_read_mesh_conf_relay_subnets(tmp_path, monkeypatch):
    cfg = _conf(tmp_path, monkeypatch,
                "  babel: true\n  mcast_mode: mesh_relay\n  mcast_source_node_ids: [47]\n")
    assert cfg["MCAST_SOURCE_SUBNETS"] == "10.20.47.0/24"
    assert cfg["MCAST_SOURCE_EXCLUDE_IPS"] == "10.20.47.1"


def test_read_mesh_conf_br_lan_mode_no_relay_subnets(tmp_path, monkeypatch):
    cfg = _conf(tmp_path, monkeypatch,
                "  mcast_mode: br_lan\n  mcast_source_node_ids: [47]\n")
    assert cfg["MCAST_SOURCE_SUBNETS"] == ""
    assert cfg["MCAST_SOURCE_EXCLUDE_IPS"] == ""


def test_default_filter_local_only():
    assert cot_bridge._tx_source_ok("10.20.42.1") is True
    assert cot_bridge._tx_source_ok("10.20.47.23") is False   # fiber peer EUD
    assert cot_bridge._tx_source_ok("10.0.0.91") is False     # eth0 WAN host


def test_relay_subnets_accepted():
    cot_bridge._apply_mcast_source_subnets({"MCAST_SOURCE_SUBNETS": "10.20.47.0/24"})
    assert cot_bridge._tx_source_ok("10.20.47.23") is True
    assert cot_bridge._tx_source_ok("10.20.42.1") is True
    assert cot_bridge._tx_source_ok("10.20.52.10") is False   # wlan1 mesh node's EUD
    assert cot_bridge._tx_source_ok("10.0.0.91") is False


def test_apply_missing_key_keeps_list_and_empty_clears():
    cot_bridge._apply_mcast_source_subnets({"MCAST_SOURCE_SUBNETS": "10.20.47.0/24"})
    cot_bridge._apply_mcast_source_subnets({})  # unreadable config
    assert cot_bridge._tx_source_ok("10.20.47.23") is True
    cot_bridge._apply_mcast_source_subnets({"MCAST_SOURCE_SUBNETS": ""})
    assert cot_bridge._tx_source_ok("10.20.47.23") is False


class _FakeSock:
    def __init__(self, pkts):
        self.pkts = list(pkts)

    def recvfrom(self, n):
        if not self.pkts:
            raise OSError
        return self.pkts.pop(0)


def test_listener_loop_bridges_relay_source(monkeypatch):
    seen = []
    monkeypatch.setattr(cot_bridge, "_tx_process_packet", lambda d: seen.append(d))
    cot_bridge._apply_mcast_source_subnets({"MCAST_SOURCE_SUBNETS": "10.20.47.0/24"})
    sock = _FakeSock([
        (b"local", ("10.20.42.11", 6969)),
        (b"fiber", ("10.20.47.23", 6969)),
        (b"mesh", ("10.20.52.10", 6969)),
    ])
    cot_bridge._mcast_listener_loop(sock, "SA")
    assert seen == [b"local", b"fiber"]


def test_source_node_lora_injection_excluded():
    # Regression: the RF-silent node's own cot-bridge (meshtastic RX on, TX off)
    # injects received LoRa CoT on its br-lan from its gateway address
    # 10.20.<id>.1 (live 0042: `(10.20.42.1,239.2.3.1) Iif: br-lan`). That copy
    # crosses the wire and must never be re-sent to LoRa by the relay node.
    cot_bridge._apply_mcast_source_subnets({
        "MCAST_SOURCE_SUBNETS": "10.20.47.0/24",
        "MCAST_SOURCE_EXCLUDE_IPS": "10.20.47.1",
    })
    assert cot_bridge._tx_source_ok("10.20.47.1") is False   # A's LoRa injection
    assert cot_bridge._tx_source_ok("10.20.47.23") is True   # A's EUD
    assert cot_bridge._tx_source_ok("10.20.42.1") is True    # own br-lan unaffected


def test_listener_loop_drops_source_node_injection(monkeypatch):
    seen = []
    monkeypatch.setattr(cot_bridge, "_tx_process_packet", lambda d: seen.append(d))
    cot_bridge._apply_mcast_source_subnets({
        "MCAST_SOURCE_SUBNETS": "10.20.47.0/24",
        "MCAST_SOURCE_EXCLUDE_IPS": "10.20.47.1",
    })
    sock = _FakeSock([
        (b"A-lora-inject", ("10.20.47.1", 6969)),
        (b"A-eud", ("10.20.47.23", 6969)),
    ])
    cot_bridge._mcast_listener_loop(sock, "SA")
    assert seen == [b"A-eud"]
