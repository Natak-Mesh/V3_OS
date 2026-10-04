"""Tests for the Reticulum/LXMF direct-message lane.

Two layers, both off-box (no rnsd, no RNS stack started):

  * ``rns_proto`` — the pure wire format + peer table, exercised with real
    msgpack announce bytes produced by the same codec the live code uses.
  * ``rns_lane`` — the safety guard that it NEVER calls RNS.Reticulum() when the
    rnsd shared instance is down (which would promote us to the instance and
    open every interface — a second stack).
  * ``rns_store`` — the per-peer conversation store.
  * schema — the derived announce app_data.
"""

import sys
import types

import pytest

from nucleusd.messaging import rns_proto as proto
from nucleusd.messaging.rns_store import ConversationStore
from nucleusd.schema import NucleusConfig


# ── pure wire format ─────────────────────────────────────────────
def _appdata(**over):
    base = {"v": 1, "id": 42, "host": "0042-nucleus",
            "mesh_ip": "10.20.1.42", "br_lan": "10.20.42.1",
            "sw": "0.9.4", "caps": ["msg", "voice"]}
    base.update(over)
    return base


def test_appdata_roundtrip():
    data = proto.encode_node_appdata(_appdata())
    assert isinstance(data, bytes)
    assert proto.parse_node_appdata(data) == _appdata()


def test_appdata_too_large_raises():
    # A caps list big enough to blow the 300B ceiling must fail loudly.
    big = _appdata(caps=[f"capability-{i}" for i in range(60)])
    with pytest.raises(ValueError):
        proto.encode_node_appdata(big)


def test_parse_rejects_garbage():
    assert proto.parse_node_appdata(None) is None
    assert proto.parse_node_appdata(b"") is None
    assert proto.parse_node_appdata(b"\xff\xff\xffnot-msgpack") is None


def test_parse_rejects_non_dict():
    mp = proto._umsgpack()
    assert proto.parse_node_appdata(mp.packb([1, 2, 3])) is None


def test_parse_rejects_unknown_version():
    # A future payload version must be ignored, not misread.
    future = proto.encode_node_appdata(_appdata(v=2)) if False else None
    mp = proto._umsgpack()
    assert proto.parse_node_appdata(mp.packb(_appdata(v=2))) is None


# ── peer table ───────────────────────────────────────────────────
def test_peer_table_update_and_list():
    pt = proto.PeerTable()
    pt.update(_appdata(id=7, host="0007-nucleus"), "aabb", hops=2, ts=100.0)
    pt.update(_appdata(id=9, host="0009-nucleus"), "ccdd", hops=1, ts=200.0)
    peers = pt.as_list()
    assert [p["id"] for p in peers] == [9, 7]          # newest-seen first
    assert peers[0]["lxmf_hash"] == "ccdd"
    assert peers[0]["hops"] == 1


def test_peer_table_refresh_replaces():
    pt = proto.PeerTable()
    pt.update(_appdata(id=7, sw="0.9.3"), "aabb", ts=100.0)
    pt.update(_appdata(id=7, sw="0.9.4"), "aabb", ts=150.0)
    peers = pt.as_list()
    assert len(peers) == 1 and peers[0]["sw"] == "0.9.4"
    assert peers[0]["last_seen"] == 150.0


def test_peer_table_ages_out():
    pt = proto.PeerTable()
    pt.update(_appdata(id=7), "aabb", ts=100.0)
    assert pt.as_list(max_age_secs=60, now=1000.0) == []
    assert len(pt.as_list(max_age_secs=60, now=130.0)) == 1


# ── per-peer store ───────────────────────────────────────────────
def test_conversation_store_per_peer():
    cs = ConversationStore(history_limit=10)
    cs.add("peerA", "hi", "out", ts=1.0, state="outbound")
    cs.add("peerA", "yo", "in", ts=2.0)
    cs.add("peerB", "sup", "in", ts=3.0)
    assert [m["text"] for m in cs.history("peerA")] == ["hi", "yo"]
    assert cs.history("peerA")[0]["direction"] == "out"
    assert len(cs.history()) == 3                        # all peers merged
    assert set(cs.peers()) == {"peerA", "peerB"}


def test_conversation_store_persist(tmp_path):
    p = str(tmp_path / "dm.jsonl")
    cs = ConversationStore(history_limit=10, persist_path=p)
    cs.add("peerA", "persisted", "in", ts=5.0)
    cs2 = ConversationStore(history_limit=10, persist_path=p)
    assert [m["text"] for m in cs2.history("peerA")] == ["persisted"]


# ── derived announce app_data (schema) ───────────────────────────
def _cfg():
    return NucleusConfig.model_validate({
        "mesh": {"password": "testpass1"},
        "ap": {"password": "appass12"},
        "node": {"id": 42},
    })


def test_schema_announce_appdata_is_derived():
    cfg = _cfg()
    data = cfg.rns_announce_appdata("0.9.4")
    assert data["id"] == 42
    assert data["host"] == "0042-nucleus"
    assert data["mesh_ip"] == "10.20.1.42"
    assert data["br_lan"] == "10.20.42.1"
    assert data["sw"] == "0.9.4"
    assert "msg" in data["caps"]
    # whatever the schema derives must survive the wire codec within budget
    assert proto.parse_node_appdata(proto.encode_node_appdata(data)) == data


def test_schema_rns_defaults_off():
    cfg = _cfg()
    assert cfg.messaging.rns.enabled is False
    assert cfg.messaging.rns.propagation_node is False


# ── safety guard: never start a second stack when rnsd is down ───
def test_lane_never_inits_reticulum_when_rnsd_down(monkeypatch):
    """If rnsd is unreachable, start() must return False and NEVER construct
    RNS.Reticulum() (doing so would promote us to the shared instance)."""
    from nucleusd.messaging import rns_lane

    # Pretend rnsd is down.
    monkeypatch.setattr(rns_lane, "rnsd_available", lambda: False)

    # Install a fake RNS module whose Reticulum() blows up if ever called.
    called = {"reticulum": False}

    def _boom(*a, **k):
        called["reticulum"] = True
        raise AssertionError("RNS.Reticulum() must not be called when rnsd is down")

    fake_rns = types.ModuleType("RNS")
    fake_rns.Reticulum = _boom
    monkeypatch.setitem(sys.modules, "RNS", fake_rns)

    lane = rns_lane.RnsLane(appdata=_appdata(), display_name="0042-nucleus")
    assert lane.start() is False
    assert lane.started is False
    assert called["reticulum"] is False
