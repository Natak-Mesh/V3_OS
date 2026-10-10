"""CoT bridge node-dump tests. Run off-box: `pytest` from repo root.

Regression for the presence-heartbeat bug: a node that is heard only via bare
heartbeat packets (HEARTBEAT_PORTNUM) is never added to the meshtastic
library's iface.nodes (that only happens on a NODEINFO_APP packet), so the old
_dump_nodes() — which iterated iface.nodes alone — dropped it from the web node
list despite its heartbeats arriving. _dump_nodes() now lists the union of
iface.nodes and _node_last_seen, synthesizing a minimal identity for
heartbeat-only nodes.

The bridge module imports `from pubsub import pub` at top level; that package
lives in the production venv (/opt/nucleus/venv) but not the dev .venv, so we
stub it in sys.modules before importing.
"""

import json
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

LOCAL_NUM = 805795615          # 0x3007771f, this node (0042)
PEER_NODEINFO_NUM = 567591628  # 0x21d4c2cc, 0052 — has NODEINFO in iface.nodes
HEARTBEAT_ONLY_NUM = 1217386307  # 0x488fd743, 0053 — heard only via heartbeat


class _FakeIface:
    """Minimal stand-in for a meshtastic TCPInterface for _dump_nodes()."""

    def __init__(self, nodes):
        self.nodes = nodes

    def getLongName(self):
        return "0042-nucleus"


def _nodeinfo_node(num, short, long_name, *, last_heard, snr=11.5, hops=0):
    """A node entry shaped like the meshtastic library's iface.nodes value."""
    return {
        "num": num,
        "user": {
            "id": f"!{num:08x}",
            "shortName": short,
            "longName": long_name,
        },
        "lastHeard": last_heard,
        "snr": snr,
        "hopsAway": hops,
    }


@pytest.fixture
def dump_env(tmp_path, monkeypatch):
    """Point NODE_DUMP_PATH at a temp file and clear shared state."""
    out = tmp_path / "meshtastic_nodes.json"
    monkeypatch.setattr(cot_bridge, "NODE_DUMP_PATH", str(out))
    monkeypatch.setattr(cot_bridge, "my_node_num", LOCAL_NUM)
    cot_bridge._node_last_seen.clear()
    cot_bridge._node_names.clear()
    # Freeze time so NODE_MAX_AGE math is deterministic.
    now = 1_791_650_000
    monkeypatch.setattr(cot_bridge.time, "time", lambda: now)
    return out, now


def _read_dump(path):
    with open(path) as f:
        return json.load(f)


def test_heartbeat_only_node_appears(dump_env, monkeypatch):
    out, now = dump_env
    # Library knows 0052 (it sent NODEINFO); 0053 has only sent heartbeats.
    iface = _FakeIface({
        "!21d4c2cc": _nodeinfo_node(
            PEER_NODEINFO_NUM, "0052", "0052-nucleus", last_heard=now - 5
        ),
    })
    monkeypatch.setattr(cot_bridge, "iface", iface)
    cot_bridge._node_last_seen[HEARTBEAT_ONLY_NUM] = now - 10

    cot_bridge._dump_nodes()
    nodes = {n["id"]: n for n in _read_dump(out)["nodes"]}

    # Both the NODEINFO node and the heartbeat-only node are present.
    assert set(nodes) == {"!21d4c2cc", "!488fd743"}
    # NODEINFO node keeps its real name.
    assert nodes["!21d4c2cc"]["long_name"] == "0052-nucleus"
    # Heartbeat-only node gets a synthesized identity from its node number.
    hb = nodes["!488fd743"]
    assert hb["short_name"] == "d743"
    assert hb["long_name"] == "Meshtastic d743"
    assert hb["last_heard"] == now - 10
    assert hb["snr"] is None
    assert hb["hops_away"] is None


def test_heartbeat_refreshes_nodeinfo_node_last_heard(dump_env, monkeypatch):
    out, now = dump_env
    # Library's lastHeard is stale; a fresher heartbeat was tracked by us.
    iface = _FakeIface({
        "!21d4c2cc": _nodeinfo_node(
            PEER_NODEINFO_NUM, "0052", "0052-nucleus", last_heard=now - 600
        ),
    })
    monkeypatch.setattr(cot_bridge, "iface", iface)
    cot_bridge._node_last_seen[PEER_NODEINFO_NUM] = now - 3

    cot_bridge._dump_nodes()
    nodes = {n["id"]: n for n in _read_dump(out)["nodes"]}
    assert nodes["!21d4c2cc"]["last_heard"] == now - 3


def test_stale_heartbeat_only_node_excluded(dump_env, monkeypatch):
    out, now = dump_env
    monkeypatch.setattr(cot_bridge, "iface", _FakeIface({}))
    # Older than NODE_MAX_AGE → dropped.
    cot_bridge._node_last_seen[HEARTBEAT_ONLY_NUM] = now - (cot_bridge.NODE_MAX_AGE + 1)

    cot_bridge._dump_nodes()
    assert _read_dump(out)["nodes"] == []


def test_local_node_excluded(dump_env, monkeypatch):
    out, now = dump_env
    monkeypatch.setattr(cot_bridge, "iface", _FakeIface({}))
    # Our own node number in _node_last_seen must never be listed.
    cot_bridge._node_last_seen[LOCAL_NUM] = now - 1

    cot_bridge._dump_nodes()
    assert _read_dump(out)["nodes"] == []


def _placeholder_node(num, *, last_heard, snr=12.5, hops=0):
    """The minimal entry the meshtastic library synthesizes for a node it has
    heard a packet from but never a NODEINFO_APP (see
    MeshInterface._getOrCreateByNum): a 'Meshtastic <hex>' name, hwModel UNSET.
    """
    presumptive_id = f"!{num:08x}"
    return {
        "num": num,
        "user": {
            "id": presumptive_id,
            "shortName": presumptive_id[-4:],
            "longName": f"Meshtastic {presumptive_id[-4:]}",
            "hwModel": "UNSET",
        },
        "lastHeard": last_heard,
        "snr": snr,
        "hopsAway": hops,
    }


def test_heartbeat_v2_names_used_for_heartbeat_only_node(dump_env, monkeypatch):
    out, now = dump_env
    monkeypatch.setattr(cot_bridge, "iface", _FakeIface({}))
    # 0053 heard only via a v2 heartbeat that carried its real names.
    cot_bridge._node_last_seen[HEARTBEAT_ONLY_NUM] = now - 10
    cot_bridge._node_names[HEARTBEAT_ONLY_NUM] = {
        "short_name": "0053",
        "long_name": "0053-nucleus",
    }

    cot_bridge._dump_nodes()
    nodes = {n["id"]: n for n in _read_dump(out)["nodes"]}

    hb = nodes["!488fd743"]
    assert hb["short_name"] == "0053"
    assert hb["long_name"] == "0053-nucleus"


def test_heartbeat_names_override_library_placeholder(dump_env, monkeypatch):
    """Regression: the library synthesizes a 'Meshtastic <hex>' placeholder for
    any heard node, so a heartbeat-only node IS in iface.nodes. The heartbeat
    names must still win over that placeholder; SNR/hops stay from the library.
    """
    out, now = dump_env
    iface = _FakeIface({
        "!488fd743": _placeholder_node(
            HEARTBEAT_ONLY_NUM, last_heard=now - 10, snr=12.5, hops=0
        ),
    })
    monkeypatch.setattr(cot_bridge, "iface", iface)
    cot_bridge._node_last_seen[HEARTBEAT_ONLY_NUM] = now - 10
    cot_bridge._node_names[HEARTBEAT_ONLY_NUM] = {
        "short_name": "0053",
        "long_name": "0053-nucleus",
    }

    cot_bridge._dump_nodes()
    nodes = {n["id"]: n for n in _read_dump(out)["nodes"]}

    hb = nodes["!488fd743"]
    assert hb["short_name"] == "0053"
    assert hb["long_name"] == "0053-nucleus"
    # Signal metrics still come from the library entry.
    assert hb["snr"] == 12.5
    assert hb["hops_away"] == 0


def test_nodeinfo_name_kept_when_no_heartbeat(dump_env, monkeypatch):
    """A node with a real NODEINFO name and no heartbeat keeps its real name."""
    out, now = dump_env
    iface = _FakeIface({
        "!21d4c2cc": _nodeinfo_node(
            PEER_NODEINFO_NUM, "0052", "0052-nucleus", last_heard=now - 5
        ),
    })
    monkeypatch.setattr(cot_bridge, "iface", iface)

    cot_bridge._dump_nodes()
    nodes = {n["id"]: n for n in _read_dump(out)["nodes"]}
    assert nodes["!21d4c2cc"]["long_name"] == "0052-nucleus"


def test_parse_heartbeat_names_v2():
    cot_bridge._node_names.clear()
    payload = b"\x02" + b"0053" + b"\x00" + b"0053-nucleus"
    cot_bridge._parse_heartbeat_names(HEARTBEAT_ONLY_NUM, payload)
    assert cot_bridge._node_names[HEARTBEAT_ONLY_NUM] == {
        "short_name": "0053",
        "long_name": "0053-nucleus",
    }


def test_parse_heartbeat_names_v1_ignored():
    cot_bridge._node_names.clear()
    # v1 bare beacon carries no names → nothing recorded.
    cot_bridge._parse_heartbeat_names(HEARTBEAT_ONLY_NUM, b"\x01")
    assert HEARTBEAT_ONLY_NUM not in cot_bridge._node_names


def test_parse_heartbeat_names_malformed_ignored():
    cot_bridge._node_names.clear()
    # v2 marker but no NUL separator → ignored, no crash.
    cot_bridge._parse_heartbeat_names(HEARTBEAT_ONLY_NUM, b"\x02nosep")
    assert HEARTBEAT_ONLY_NUM not in cot_bridge._node_names
    # Empty payload → ignored.
    cot_bridge._parse_heartbeat_names(HEARTBEAT_ONLY_NUM, b"")
    assert HEARTBEAT_ONLY_NUM not in cot_bridge._node_names


def test_build_heartbeat_payload_v2():
    class _NamedIface:
        def getShortName(self):
            return "0042"

        def getLongName(self):
            return "0042-nucleus"

    cot_bridge.iface = _NamedIface()
    try:
        payload = cot_bridge._build_heartbeat_payload()
    finally:
        cot_bridge.iface = None
    assert payload == b"\x02" + b"0042" + b"\x00" + b"0042-nucleus"
    # Round-trips through the parser.
    cot_bridge._node_names.clear()
    cot_bridge._parse_heartbeat_names(LOCAL_NUM, payload)
    assert cot_bridge._node_names[LOCAL_NUM]["long_name"] == "0042-nucleus"


def test_build_heartbeat_payload_falls_back_to_v1():
    class _NoNameIface:
        def getShortName(self):
            return ""

        def getLongName(self):
            return ""

    cot_bridge.iface = _NoNameIface()
    try:
        assert cot_bridge._build_heartbeat_payload() == b"\x01"
    finally:
        cot_bridge.iface = None


def test_local_node_excluded_by_long_name(dump_env, monkeypatch):
    out, now = dump_env
    # A ghost client entry sharing our long name but a different num.
    ghost_num = 999999999
    iface = _FakeIface({
        "!3b9ac9ff": _nodeinfo_node(
            ghost_num, "0042", "0042-nucleus", last_heard=now - 1
        ),
    })
    monkeypatch.setattr(cot_bridge, "iface", iface)

    cot_bridge._dump_nodes()
    assert _read_dump(out)["nodes"] == []
