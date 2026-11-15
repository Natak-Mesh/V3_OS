"""Tests for the Nucleus Control rnsd plugin + its client (manual announces on
access_point radios).

The plugin (system/reticulum/NucleusCtlInterface.py) is loaded the way rnsd
loads external interfaces: exec() with `Interface` and `RNS` in globals. The
announce fixture is a real lxmf.delivery announce captured from rnsd's cache on
node 0042 (~/.reticulum/storage/cache/announces/, 2026-11-15), so the resend is
checked against the true RNS 1.5.x wire format.
"""

import json
import socket
import threading
import types
import uuid
from pathlib import Path

import pytest
import RNS
from RNS.Interfaces.Interface import Interface

from nucleusd import reticulum as reti

PLUGIN = Path(__file__).resolve().parent.parent / "system" / "reticulum" / "NucleusCtlInterface.py"

# Real cached announce (raw packet) for lxmf.delivery ea10e4927096acacd67a9eb1d16c0498.
ANNOUNCE_RAW = bytes.fromhex(
    "2100ea10e4927096acacd67a9eb1d16c04980b13f9b3788f111055793d6dfcdc863db603"
    "0c5719f76dfe553dc2c46401192f6309a0dcb81c19bbbef95898db5aa14cd1843f495399"
    "fcae16d06faa21f66698d56ec60bc318e2c0f0d90841a2d6904d006af99ee351cfe3164a"
    "39b6aebe07d94d2a7ba12a0a2f67f24263b537c4e33f064b48f529250dcb633ac78b91c1"
    "15464c793a4a7b6f1c83ef0992b5178d38a8246ed4588aa8beaecd10f366280530276541"
    "267d6e77412d9e90be91b078970fcbc7ac440c93c40c303034322d6e75636c657573c09100"
)
DEST = "ea10e4927096acacd67a9eb1d16c0498"


def _load_plugin():
    g = {"Interface": Interface, "RNS": RNS}
    exec(PLUGIN.read_text(), g)
    return g


class _Radio:
    """Stand-in for an rnsd interface."""

    def __init__(self, name, online=True):
        self.name, self.online, self.parent_interface = name, online, None


class _Pkt:
    """Records the packet the plugin builds instead of transmitting it."""
    sent: list = []

    def __init__(self, dest, data, ptype, **kw):
        self.dest, self.data, self.ptype, self.kw = dest, data, ptype, kw

    def send(self):
        _Pkt.sent.append(self)


@pytest.fixture
def plugin():
    g = _load_plugin()
    rnode, mesh = _Radio("RNode LoRa"), _Radio("Mesh AutoInterface")
    _Pkt.sent = []
    pkt_cls = type("Packet", (_Pkt,), {"ANNOUNCE": RNS.Packet.ANNOUNCE,
                                       "NONE": RNS.Packet.NONE,
                                       "HEADER_2": RNS.Packet.HEADER_2})
    transport = types.SimpleNamespace(
        interfaces=[mesh, rnode],
        # Real rnsd path_table entry layout: hops at [2], packet hash at [6].
        path_table={bytes.fromhex(DEST): [0, b"", 1, 0, [], mesh, b"h" * 32]},
        get_cached_packet=lambda h, packet_type=None: RNS.Packet(None, ANNOUNCE_RAW),
        identity=types.SimpleNamespace(hash=bytes(16)),
        TRANSPORT=RNS.Transport.TRANSPORT,
    )
    g["RNS"] = types.SimpleNamespace(
        Transport=transport,
        Identity=types.SimpleNamespace(recall=lambda h: object()),
        Destination=type("Destination", (), {"OUT": 0, "SINGLE": 0,
                                             "__init__": lambda self, *a: None}),
        Packet=pkt_cls,
        log=lambda *a, **k: None,
        prettyhexrep=RNS.prettyhexrep,
        LOG_NOTICE=RNS.LOG_NOTICE,
    )
    return g, rnode, transport


def test_resend_targets_only_named_interface(plugin):
    g, rnode, _ = plugin
    ok, err = g["resend_announce"](DEST, "RNode LoRa")
    assert ok and err is None
    assert len(_Pkt.sent) == 1
    p = _Pkt.sent[0]
    assert p.kw["attached_interface"] is rnode   # bypasses the access_point block
    assert p.ptype == RNS.Packet.ANNOUNCE
    assert p.dest.hash == bytes.fromhex(DEST)
    assert p.hops == 1
    # Payload is the stored announce data, unchanged (signature still valid).
    stored = RNS.Packet(None, ANNOUNCE_RAW)
    assert stored.unpack()
    assert p.data == stored.data
    assert p.kw["context_flag"] == stored.context_flag


def test_resend_refuses_down_unknown_and_unheld(plugin):
    g, rnode, _ = plugin
    assert g["resend_announce"](DEST, "Nope")[0] is False
    rnode.online = False
    ok, err = g["resend_announce"](DEST, "RNode LoRa")
    assert not ok and "down" in err
    rnode.online = True
    ok, err = g["resend_announce"]("00" * 16, "RNode LoRa")
    assert not ok and "no announce" in err
    assert g["resend_announce"]("zz", "RNode LoRa")[0] is False
    assert _Pkt.sent == []


def test_handle_request_protocol(plugin):
    g, _, _ = plugin
    req = json.dumps({"cmd": "announce_on", "interface": "RNode LoRa", "dest": DEST}).encode()
    assert g["handle_request"](req) == {"ok": True}
    assert g["handle_request"](b"not json")["ok"] is False
    assert g["handle_request"](b'{"cmd":"other"}')["ok"] is False


def test_ctl_addr_matches_client():
    assert _load_plugin()["CTL_ADDR"] == reti.CTL_ADDR


def test_client_roundtrip_over_real_socket(monkeypatch):
    """reti.announce_on <-> the plugin's socket server, end to end."""
    g = _load_plugin()
    addr = f"\0nucleus-test/{uuid.uuid4().hex}"
    seen = []
    g["resend_announce"] = lambda d, i: (seen.append((d, i)), (True, None))[1]
    cls = g["NucleusCtlInterface"]
    iface = cls.__new__(cls)   # skip Interface.__init__ (needs a live Reticulum)
    iface._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    iface._sock.bind(addr)
    iface._sock.listen(1)
    threading.Thread(target=iface._serve, daemon=True).start()
    monkeypatch.setattr(reti, "CTL_ADDR", addr)
    try:
        assert reti.announce_on("RNode LoRa", DEST) == {"ok": True}
        assert seen == [(DEST, "RNode LoRa")]
    finally:
        iface._sock.close()


def test_client_unreachable_raises(monkeypatch):
    monkeypatch.setattr(reti, "CTL_ADDR", f"\0nucleus-test/{uuid.uuid4().hex}")
    with pytest.raises(reti.ReticulumError):
        reti.announce_on("RNode LoRa", DEST)
