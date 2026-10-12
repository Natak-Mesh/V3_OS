"""CoT bridge receive-only gate (meshtastic.tx_enabled). Run off-box: `pytest`.

When tx_enabled is false the radio stays powered and keeps RX, but the bridge
must not queue ANY outbound LoRa packet (CoT/text/voice/stream/heartbeat) the
firmware would drop. These tests pin:
  * _apply_tx_enabled() flips the live gate from a _read_mesh_conf() dict,
  * _read_mesh_conf() surfaces TX_ENABLED from config.yaml,
  * tx_allowed() gates the CoT TX path and the heartbeat send.
The production-only `pubsub` package is stubbed so the module imports off-box.
"""

import sys
import types

# ── Stub the production-only `pubsub` package so the module imports off-box ──
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


@pytest.fixture(autouse=True)
def _restore_gate():
    """Each test leaves TX_ENABLED back at its default to avoid cross-talk."""
    saved = cot_bridge.TX_ENABLED
    yield
    cot_bridge.TX_ENABLED = saved


def test_apply_tx_enabled_flips_gate():
    cot_bridge.TX_ENABLED = True
    cot_bridge._apply_tx_enabled({"TX_ENABLED": "false"})
    assert cot_bridge.tx_allowed() is False
    cot_bridge._apply_tx_enabled({"TX_ENABLED": "true"})
    assert cot_bridge.tx_allowed() is True


def test_apply_tx_enabled_missing_key_leaves_gate_unchanged():
    cot_bridge.TX_ENABLED = False
    cot_bridge._apply_tx_enabled({})  # no TX_ENABLED key
    assert cot_bridge.tx_allowed() is False


def test_read_mesh_conf_surfaces_tx_enabled(tmp_path, monkeypatch):
    conf = tmp_path / "config.yaml"
    conf.write_text("meshtastic:\n  enabled: true\n  tx_enabled: false\n")
    monkeypatch.setattr(cot_bridge, "MESH_CONF", str(conf))
    cfg = cot_bridge._read_mesh_conf()
    assert cfg["TX_ENABLED"] == "false"


def test_read_mesh_conf_defaults_tx_enabled_true(tmp_path, monkeypatch):
    conf = tmp_path / "config.yaml"
    conf.write_text("meshtastic:\n  enabled: true\n")  # no tx_enabled key
    monkeypatch.setattr(cot_bridge, "MESH_CONF", str(conf))
    cfg = cot_bridge._read_mesh_conf()
    assert cfg["TX_ENABLED"] == "true"


class _FakeIface:
    def __init__(self):
        self.sent = []

    def sendData(self, *a, **k):
        self.sent.append((a, k))


def test_cot_tx_blocked_when_receive_only(monkeypatch):
    # tx disabled -> _tx_process_packet drops before touching the radio.
    cot_bridge.TX_ENABLED = False
    fake = _FakeIface()
    monkeypatch.setattr(cot_bridge, "iface", fake)
    before = cot_bridge.stats["tx_mcast_received"]
    cot_bridge._tx_process_packet(b"\x00\x01\x02")
    assert fake.sent == []  # nothing transmitted
    assert cot_bridge.stats["tx_mcast_received"] == before + 1  # RX still counted


def test_heartbeat_skipped_when_receive_only(monkeypatch):
    cot_bridge.TX_ENABLED = False
    fake = _FakeIface()
    monkeypatch.setattr(cot_bridge, "iface", fake)
    cot_bridge._send_heartbeat()
    assert fake.sent == []  # no heartbeat transmitted in receive-only mode
