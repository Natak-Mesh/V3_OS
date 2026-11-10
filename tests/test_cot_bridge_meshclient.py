"""cot-bridge Meshtastic client wiring. Run off-box: `pytest` from repo root.

Regression for the LoRa text leak: onReceive() forwarded EVERY text packet
(DMs, secondary channels, emoji reactions) to nucleus-messaging as plain chat.
Now all text goes to the client chat store and only plain primary-channel
broadcasts are forwarded. Fixtures are real meshtastic-lib packet dicts.
"""

import json
import struct
import sys
import types

import pytest

if "pubsub" not in sys.modules:
    _pubsub = types.ModuleType("pubsub")
    _pubsub.pub = types.SimpleNamespace(
        subscribe=lambda *a, **k: None,
        sendMessage=lambda *a, **k: None,
        AUTO_TOPIC=object(),
    )
    sys.modules["pubsub"] = _pubsub

from nucleusd.meshtastic import cot_bridge, meshchat  # noqa: E402

from tests.meshtastic_fixtures import (  # noqa: E402
    ACK, CH1_BROADCAST, DM_REACTION, ME, PRIMARY_BROADCAST,
)


class _FwdSock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class _Iface:
    nodes = {}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    store = meshchat.ChatStore(str(tmp_path / "chat.db"))
    fwd = _FwdSock()
    monkeypatch.setattr(cot_bridge, "_chat_store", store)
    monkeypatch.setattr(cot_bridge, "text_fwd_sock", fwd)
    monkeypatch.setattr(cot_bridge, "my_node_num", ME)
    monkeypatch.setattr(cot_bridge, "iface", _Iface())
    return store, fwd


def test_primary_broadcast_stored_and_forwarded(bridge):
    store, fwd = bridge
    cot_bridge.onReceive(dict(PRIMARY_BROADCAST), None)
    assert store.messages("ch0")[0]["text"] == "hello all"
    assert len(fwd.sent) == 1
    data, addr = fwd.sent[0]
    assert addr == cot_bridge.TEXT_RELAY_FORWARD
    n = struct.unpack("<B", data[:1])[0]
    assert data[1 + n:] == b"hello all"


def test_secondary_channel_stored_not_forwarded(bridge):
    store, fwd = bridge
    cot_bridge.onReceive(dict(CH1_BROADCAST), None)
    assert store.messages("ch1")[0]["text"] == "hello ch1"
    assert fwd.sent == []


def test_dm_reaction_stored_not_forwarded(bridge):
    store, fwd = bridge
    store.add("!21d4c2cc", ME, "hi", 1.0, 999, mine=True)
    cot_bridge.onReceive(dict(DM_REACTION), None)
    assert store.messages("!21d4c2cc")[0]["reactions"][0]["emoji"] == "👍"
    assert fwd.sent == []


def test_routing_ack_marks_dm_delivered(bridge):
    store, _ = bridge
    store.add("!21d4c2cc", ME, "hi", 1.0, 777, mine=True, state="sending")
    cot_bridge.onReceive(dict(ACK), None)
    assert store.messages("!21d4c2cc")[0]["state"] == "delivered"


class _CtlSock:
    def __init__(self, req):
        self._q = [(json.dumps(req).encode(), ("127.0.0.1", 40000))]
        self.sent = []

    def recvfrom(self, _n):
        if self._q:
            return self._q.pop(0)
        raise OSError("closed")

    def sendto(self, data, addr):
        self.sent.append(json.loads(data))


def test_control_rejects_unknown_command(bridge):
    sock = _CtlSock({"cmd": "nope"})
    cot_bridge._client_control_loop(sock)
    assert sock.sent == [{"ok": False, "error": "unknown command"}]


def test_control_refuses_when_receive_only(bridge, monkeypatch):
    monkeypatch.setattr(cot_bridge, "TX_ENABLED", False)
    sock = _CtlSock({"cmd": "send", "chat": "ch0", "text": "x"})
    cot_bridge._client_control_loop(sock)
    assert sock.sent == [{"ok": False, "error": "radio is receive-only"}]


def test_control_bad_node_id(bridge):
    sock = _CtlSock({"cmd": "traceroute", "node": "bogus"})
    cot_bridge._client_control_loop(sock)
    assert sock.sent[0]["ok"] is False and "bad node id" in sock.sent[0]["error"]


def test_traceroute_records_round_trip_time(bridge, monkeypatch):
    """Link test: a traceroute reply stores the request->reply time (rtt_s)."""
    from tests.meshtastic_fixtures import PEER, TRACEROUTE
    # meshtastic isn't in the dev venv: stub the two protobuf names it imports.
    pb = types.ModuleType("meshtastic.protobuf")
    pb.mesh_pb2 = types.SimpleNamespace(RouteDiscovery=lambda: b"")
    pb.portnums_pb2 = types.SimpleNamespace(
        PortNum=types.SimpleNamespace(TRACEROUTE_APP=70))
    monkeypatch.setitem(sys.modules, "meshtastic", types.ModuleType("meshtastic"))
    monkeypatch.setitem(sys.modules, "meshtastic.protobuf", pb)
    sent = {}
    monkeypatch.setattr(cot_bridge, "_client_send_packet",
                        lambda *a, on_response=None, **k: sent.update(cb=on_response))
    monkeypatch.setattr(cot_bridge, "_dump_client_nodes", lambda: None)
    clock = iter([1000.0, 1000.0, 1007.25, 1007.25])
    monkeypatch.setattr(cot_bridge.time, "time", lambda: next(clock))
    cot_bridge._client_traceroute("!21d4c2cc", PEER)
    assert cot_bridge._traceroutes["!21d4c2cc"]["pending"] is True
    sent["cb"]({"from": PEER, "decoded": dict(TRACEROUTE)})
    tr = cot_bridge._traceroutes["!21d4c2cc"]
    assert tr["rtt_s"] == 7.25
    assert tr["route"]["towards"][-1] == "!21d4c2cc"
