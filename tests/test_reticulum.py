"""Tests for nucleusd.reticulum — shared-instance RPC parsing.

All tests mock `_rpc` so nothing opens the real rnsd control socket. Fixtures use
the real `{"get": "interface_stats"}` / `{"get": "path_table"}` reply shapes
captured from a live rnsd (RNS 1.5.x) on a node.
"""

import pytest

from nucleusd import reticulum as reti


# Trimmed-but-real interface_stats reply: a shared instance (mode 1), the mesh
# AutoInterface (mode 7 internal), the LAN TCP server (mode 7), the boundary
# Entry Node uplink (mode 5), plus one ephemeral AutoInterfacePeer that must be
# filtered out of the default view.
IFACE_STATS = {
    "rxb": 324716, "txb": 656572, "rxs": 0.0, "txs": 0.0,
    "transport_id": bytes.fromhex("5fdaeff7e4257f4b641e3e1f632eebb4"),
    "transport_uptime": 1854.08,
    "interfaces": [
        {"name": "Shared Instance[rns/default]", "short_name": "Shared Instance",
         "status": True, "mode": 1, "bitrate": 1000000000, "rxb": 0, "txb": 0,
         "rxs": 0.0, "txs": 0.0, "incoming_announce_frequency": 0.0,
         "outgoing_announce_frequency": 0.0, "clients": 2},
        {"name": "AutoInterface[Mesh AutoInterface]", "short_name": "Mesh AutoInterface",
         "status": True, "mode": 7, "bitrate": 10000000, "rxb": 0, "txb": 0,
         "rxs": 0.0, "txs": 0.0, "incoming_announce_frequency": 0.0,
         "outgoing_announce_frequency": 0.0, "clients": None},
        {"name": "TCPServerInterface[LAN TCP Server/10.20.42.1:4242]",
         "short_name": "LAN TCP Server", "status": True, "mode": 7,
         "bitrate": 10000000, "rxb": 10, "txb": 20, "rxs": 0.0, "txs": 0.0,
         "incoming_announce_frequency": 0.0, "outgoing_announce_frequency": 0.0,
         "clients": 0},
        {"name": "TCPInterface[Entry Node/173.230.150.24:4243]",
         "short_name": "Entry Node", "status": True, "mode": 5,
         "bitrate": 10000000, "rxb": 325356, "txb": 657446, "rxs": 0.0, "txs": 0.0,
         "incoming_announce_frequency": 1.0, "outgoing_announce_frequency": 1.76,
         "clients": None},
        {"name": "AutoInterfacePeer[wlan1/fe80::8ae5:2ce0:5629:f96e]",
         "short_name": "AutoInterfacePeer", "status": True, "mode": 7,
         "bitrate": 10000000, "rxb": 0, "txb": 0, "rxs": 0.0, "txs": 0.0,
         "incoming_announce_frequency": 0.0, "outgoing_announce_frequency": 0.0,
         "clients": None},
    ],
}

PATH_TABLE = [
    {"hash": bytes.fromhex("3c7365767671e3f3927c99d8e5f342be"),
     "via": bytes.fromhex("4dc77e9d52a07f3e6c40e22b5774c09f"),
     "hops": 4, "expires": 1791725839.23, "timestamp": 1791121039.23,
     "interface": "TCPInterface[Entry Node/173.230.150.24:4243]"},
    {"hash": bytes.fromhex("f7d34feff7504a15fc222786b4557a0a"),
     "via": bytes.fromhex("4dc77e9d52a07f3e6c40e22b5774c09f"),
     "hops": 5, "expires": 1791726703.66, "timestamp": 1791121903.66,
     "interface": "TCPInterface[Entry Node/173.230.150.24:4243]"},
]


def _mock_rpc(monkeypatch, iface=IFACE_STATS, paths=PATH_TABLE, raise_on=None):
    def fake(request, timeout=reti._TIMEOUT):
        get = request.get("get")
        if raise_on and get == raise_on:
            raise reti.ReticulumError("mocked failure")
        if get == "interface_stats":
            return iface
        if get == "path_table":
            return paths
        raise AssertionError(f"unexpected request {request}")
    monkeypatch.setattr(reti, "_rpc", fake)



def test_interfaces_filters_ephemeral(monkeypatch):
    _mock_rpc(monkeypatch)
    ifaces = reti.interfaces()
    names = [i["name"] for i in ifaces]
    assert not any("AutoInterfacePeer[" in n for n in names)
    assert len(ifaces) == 4


def test_interfaces_include_ephemeral(monkeypatch):
    _mock_rpc(monkeypatch)
    ifaces = reti.interfaces(include_ephemeral=True)
    assert len(ifaces) == 5


def test_interface_mode_and_fields(monkeypatch):
    _mock_rpc(monkeypatch)
    by_name = {i["short_name"]: i for i in reti.interfaces()}
    assert by_name["Mesh AutoInterface"]["mode"] == "internal"
    assert by_name["Entry Node"]["mode"] == "boundary"
    entry = by_name["Entry Node"]
    assert entry["up"] is True
    assert entry["rxb"] == 325356 and entry["txb"] == 657446
    assert entry["announces_in"] == 1.0 and entry["announces_out"] == 1.76


def test_shared_instance_client_count_decremented(monkeypatch):
    _mock_rpc(monkeypatch)
    shared = next(i for i in reti.interfaces() if i["name"].startswith("Shared Instance["))
    # rnsd counts itself; 2 raw clients -> 1 attached program.
    assert shared["clients"] == 1


def test_transport_hashes_hexed(monkeypatch):
    _mock_rpc(monkeypatch)
    t = reti.transport()
    assert t["transport_id"] == "5fdaeff7e4257f4b641e3e1f632eebb4"
    assert t["uptime"] == 1854.08
    assert t["rxb"] == 324716 and t["txb"] == 656572


def test_path_table_shape(monkeypatch):
    _mock_rpc(monkeypatch)
    paths = reti.path_table()
    assert len(paths) == 2
    assert paths[0]["hash"] == "3c7365767671e3f3927c99d8e5f342be"
    assert paths[0]["via"] == "4dc77e9d52a07f3e6c40e22b5774c09f"
    assert paths[0]["hops"] == 4
    assert paths[0]["interface"].startswith("TCPInterface[Entry Node")


def test_status_snapshot(monkeypatch):
    _mock_rpc(monkeypatch)
    s = reti.status()
    assert s["running"] is True
    assert len(s["interfaces"]) == 4
    assert s["path_count"] == 2
    assert s["transport"]["transport_id"] == "5fdaeff7e4257f4b641e3e1f632eebb4"


def test_status_not_running(monkeypatch):
    # rnsd down -> _rpc raises; status() degrades to a clean stopped snapshot.
    def fake(request, timeout=reti._TIMEOUT):
        raise reti.ReticulumError("socket unreachable")
    monkeypatch.setattr(reti, "_rpc", fake)
    s = reti.status()
    assert s == {"running": False, "transport": {}, "interfaces": [], "path_count": 0}


def test_status_path_table_failure_tolerated(monkeypatch):
    # interface_stats ok but path_table RPC fails -> path_count 0, still running.
    _mock_rpc(monkeypatch, raise_on="path_table")
    s = reti.status()
    assert s["running"] is True
    assert s["path_count"] == 0
    assert len(s["interfaces"]) == 4


def test_malformed_interface_stats_raises(monkeypatch):
    monkeypatch.setattr(reti, "_rpc", lambda req, timeout=reti._TIMEOUT: ["not", "a", "dict"])
    with pytest.raises(reti.ReticulumError):
        reti.interfaces()
