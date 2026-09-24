"""CoT bridge per-UID TX rate-limit tests. Run off-box: `pytest` from repo root.

The bridge module imports `from pubsub import pub` at top level; that package
lives in the production venv (/opt/nucleus/venv) but not the dev .venv, so we
stub it in sys.modules before importing. `meshtastic` is only imported inside
functions we don't call here, so it needs no stub.
"""

import sys
import types

import pytest

# ── Stub the production-only `pubsub` package so the module imports off-box ──
if "pubsub" not in sys.modules:
    _pubsub = types.ModuleType("pubsub")
    _pubsub.pub = types.SimpleNamespace(
        subscribe=lambda *a, **k: None,
        sendMessage=lambda *a, **k: None,
        AUTO_TOPIC=object(),
    )
    sys.modules["pubsub"] = _pubsub

from nucleusd.meshtastic import cot_bridge  # noqa: E402
from nucleusd.schema import MeshtasticConfig  # noqa: E402

MESH_BLOCK = """\
meshtastic:
  enabled: true
  region: US
  hat: rak6421-slot1
  gps: uart
  gps_serial_path: /dev/ttyS0
  i2c_device: /dev/i2c-1
  cot_bridge: true
  tx_min_interval_secs: {tx}
"""


def _write_conf(tmp_path, monkeypatch, *, tx=None):
    """Write a real-format config.yaml and point the bridge's MESH_CONF at it."""
    if tx is None:
        block = "\n".join(
            l for l in MESH_BLOCK.splitlines() if "tx_min_interval_secs" not in l
        ) + "\n"
    else:
        block = MESH_BLOCK.format(tx=tx)
    p = tmp_path / "config.yaml"
    p.write_text(block)
    monkeypatch.setattr(cot_bridge, "MESH_CONF", str(p))
    return p


def test_starting_value_matches_schema_default():
    # The hardcoded default was removed; the starting value comes from the schema.
    assert cot_bridge.TX_MIN_INTERVAL == \
        MeshtasticConfig.model_fields["tx_min_interval_secs"].default


def test_read_mesh_conf_surfaces_value(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, tx=5)
    assert cot_bridge._read_mesh_conf()["TX_MIN_INTERVAL"] == "5"


def test_read_mesh_conf_omits_missing_key(tmp_path, monkeypatch):
    # Key absent → not surfaced, so _apply_ leaves the current value untouched.
    _write_conf(tmp_path, monkeypatch, tx=None)
    assert "TX_MIN_INTERVAL" not in cot_bridge._read_mesh_conf()


def test_apply_updates_global(monkeypatch):
    monkeypatch.setattr(cot_bridge, "TX_MIN_INTERVAL", 30)
    cot_bridge._apply_tx_min_interval({"TX_MIN_INTERVAL": "5"})
    assert cot_bridge.TX_MIN_INTERVAL == 5


def test_apply_bad_value_keeps_current(monkeypatch):
    monkeypatch.setattr(cot_bridge, "TX_MIN_INTERVAL", 30)
    cot_bridge._apply_tx_min_interval({"TX_MIN_INTERVAL": "not-a-number"})
    assert cot_bridge.TX_MIN_INTERVAL == 30


def test_apply_missing_key_keeps_current(monkeypatch):
    monkeypatch.setattr(cot_bridge, "TX_MIN_INTERVAL", 30)
    cot_bridge._apply_tx_min_interval({})
    assert cot_bridge.TX_MIN_INTERVAL == 30


def test_tx_rate_ok_zero_never_limits(monkeypatch):
    monkeypatch.setattr(cot_bridge, "TX_MIN_INTERVAL", 0)
    cot_bridge._tx_last_sent.clear()
    t = [1000.0]
    monkeypatch.setattr(cot_bridge.time, "time", lambda: t[0])
    assert cot_bridge._tx_rate_ok("uid-1") is True
    # Immediately again, same clock: 0s interval means never limited.
    assert cot_bridge._tx_rate_ok("uid-1") is True


def test_tx_rate_ok_enforces_interval(monkeypatch):
    monkeypatch.setattr(cot_bridge, "TX_MIN_INTERVAL", 5)
    cot_bridge._tx_last_sent.clear()
    t = [1000.0]
    monkeypatch.setattr(cot_bridge.time, "time", lambda: t[0])
    assert cot_bridge._tx_rate_ok("uid-1") is True   # first TX allowed
    t[0] = 1003.0
    assert cot_bridge._tx_rate_ok("uid-1") is False  # 3s < 5s → blocked
    t[0] = 1006.0
    assert cot_bridge._tx_rate_ok("uid-1") is True   # 6s >= 5s → allowed
