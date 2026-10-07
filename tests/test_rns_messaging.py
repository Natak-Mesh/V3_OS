"""Tests for the Reticulum/LXMF direct-message lane.

All off-box (no rnsd, no RNS stack started):

  * ``rns_proto`` — the pure contact-card wire format + the lxmf.delivery address
    derivation (pinned to real RNS keys via a committed fixture).
  * ``rns_contacts`` — the persistent contact book (verify-on-import, auto-add of
    inbound senders, key reload).
  * ``rns_lane`` — the safety guard that it NEVER calls RNS.Reticulum() when the
    rnsd shared instance is down (which would promote us to the instance and
    open every interface — a second stack).
  * ``rns_store`` — the per-peer conversation store.
  * schema — the manual/auto announce mode.
"""

import sys
import types

import pytest

from nucleusd.messaging import rns_proto as proto
from nucleusd.messaging.rns_contacts import ContactStore
from nucleusd.messaging.rns_store import ConversationStore
from nucleusd.schema import NucleusConfig


# A real RNS identity key + its true lxmf.delivery hash, generated once with the
# installed RNS 1.5.x so the pure derivation below is pinned to the library's
# actual output (see the generator note in the plan). Do not hand-edit.
FIXTURE_PUB = (
    "7653f440f728eec453f75ec19572d6d259967e48af75cd473478d3865a314a74"
    "890782c2bbbe04501c10ceb9f3a0c03deff175f7f844ab5178660d4056e3fef5"
)
FIXTURE_LXMF = "54f56bb421adbfed2932e2a8973754c5"


def _card(**over):
    base = {"v": 1, "name": "0042-nucleus", "id": 42,
            "lxmf_hash": FIXTURE_LXMF, "pubkey": FIXTURE_PUB}
    base.update(over)
    return base


# ── address derivation (pinned to real RNS) ──────────────────────
def test_lxmf_hash_matches_real_rns_fixture():
    assert proto.lxmf_delivery_hash(bytes.fromhex(FIXTURE_PUB)) == FIXTURE_LXMF


def test_lxmf_hash_rejects_bad_key_length():
    with pytest.raises(ValueError):
        proto.lxmf_delivery_hash(b"\x00" * 10)


# ── contact card wire format ─────────────────────────────────────
def test_card_roundtrip_link():
    link = proto.encode_card(_card())
    assert link.startswith("nucleus-rns://")
    back = proto.decode_card(link)
    assert back["lxmf_hash"] == FIXTURE_LXMF
    assert back["pubkey"] == FIXTURE_PUB
    assert back["name"] == "0042-nucleus" and back["id"] == 42


def test_decode_accepts_bare_token():
    link = proto.encode_card(_card())
    token = link[len("nucleus-rns://"):]
    assert proto.decode_card(token) == proto.decode_card(link)


def test_decode_rejects_garbage():
    assert proto.decode_card(None) is None
    assert proto.decode_card("") is None
    assert proto.decode_card("nucleus-rns://not-base64!!") is None
    assert proto.decode_card("http://example.com") is None


def test_verify_card_matches_and_rejects_mismatch():
    assert proto.verify_card(_card()) is True
    # Flip one nibble of the claimed address: key no longer derives it.
    bad = _card(lxmf_hash="00" + FIXTURE_LXMF[2:])
    assert proto.verify_card(bad) is False


def test_card_qr_svg_renders():
    pytest.importorskip("qrcode")  # a package dep; may be absent on a bare dev venv
    svg = proto.card_qr_svg(proto.encode_card(_card()))
    assert svg.startswith(b"<?xml") or b"<svg" in svg


# ── contact store ────────────────────────────────────────────────
def test_contact_add_card_verifies():
    cs = ContactStore()
    c = cs.add_card(_card(), source="link")
    assert c["lxmf_hash"] == FIXTURE_LXMF and c["pubkey"] == FIXTURE_PUB
    assert c["source"] == "link"
    assert [x["lxmf_hash"] for x in cs.as_list()] == [FIXTURE_LXMF]


def test_contact_add_card_rejects_mismatch():
    cs = ContactStore()
    with pytest.raises(ValueError):
        cs.add_card(_card(lxmf_hash="00" + FIXTURE_LXMF[2:]))


def test_contact_inbound_autoadd_then_upgrade():
    cs = ContactStore()
    # First contact from an inbound message with no key yet -> keyless entry.
    c = cs.add_inbound(FIXTURE_LXMF, None, ts=1.0)
    assert c["pubkey"] == "" and c["source"] == "inbound"
    # Later the key is learned -> upgraded in place, added time preserved.
    c2 = cs.add_inbound(FIXTURE_LXMF, FIXTURE_PUB, ts=2.0)
    assert c2["pubkey"] == FIXTURE_PUB and c2["added"] == 1.0
    # Only keyed contacts are offered for RNS preload.
    assert [x["lxmf_hash"] for x in cs.with_keys()] == [FIXTURE_LXMF]


def test_contact_remove_and_persist(tmp_path):
    p = str(tmp_path / "contacts.json")
    cs = ContactStore(persist_path=p)
    cs.add_card(_card())
    cs2 = ContactStore(persist_path=p)
    assert [x["lxmf_hash"] for x in cs2.as_list()] == [FIXTURE_LXMF]
    assert cs2.remove(FIXTURE_LXMF) is True
    cs3 = ContactStore(persist_path=p)
    assert cs3.as_list() == []


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


# ── schema: announce mode ────────────────────────────────────────
def _cfg(**rns):
    raw = {
        "mesh": {"password": "testpass1"},
        "ap": {"password": "appass12"},
        "node": {"id": 42},
    }
    if rns:
        raw["messaging"] = {"rns": rns}
    return NucleusConfig.model_validate(raw)


def test_schema_rns_defaults_manual_announce():
    cfg = _cfg()
    assert cfg.messaging.rns.enabled is True
    assert cfg.messaging.rns.announce_mode == "manual"
    assert cfg.messaging.rns.propagation_node is False


def test_schema_rns_announce_mode_auto_accepted():
    cfg = _cfg(announce_mode="auto", announce_interval_secs=600)
    assert cfg.messaging.rns.announce_mode == "auto"
    assert cfg.messaging.rns.announce_interval_secs == 600


def test_schema_rns_announce_mode_rejects_garbage():
    with pytest.raises(Exception):
        _cfg(announce_mode="sometimes")


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

    lane = rns_lane.RnsLane(display_name="0042-nucleus")
    assert lane.start() is False
    assert lane.started is False
    assert called["reticulum"] is False


# ── contact preload: known contacts are taught to RNS on start ───
def test_lane_preloads_contacts_into_rns():
    """_preload_contacts must call Identity.remember for every keyed contact so
    sends resolve without ever hearing an announce (the core of the rework)."""
    from nucleusd.messaging import rns_lane

    remembered = []

    class _FakeIdentity:
        @staticmethod
        def remember(packet_hash, dest_hash, pub, app_data=None):
            remembered.append((dest_hash.hex(), pub.hex()))

    fake_rns = types.ModuleType("RNS")
    fake_rns.Identity = _FakeIdentity
    import sys as _sys
    _sys.modules["RNS"] = fake_rns
    try:
        contacts = ContactStore()
        contacts.add_card(_card())
        lane = rns_lane.RnsLane(display_name="n", contacts=contacts)
        lane._preload_contacts()
    finally:
        del _sys.modules["RNS"]

    assert remembered == [(FIXTURE_LXMF, FIXTURE_PUB)]


# ── path discovery is explicit and send emits nothing on its own ─
class _FakeTransport:
    """Records request_path calls and answers has_path from a fixed set."""

    requested: list = []
    paths: set = set()
    hops_map: dict = {}
    # Real rnsd path-table shape: dest_hash(bytes) -> list whose IDX_PT_TIMESTAMP
    # (0) slot is the epoch the path was recorded. path_info reads it for age.
    IDX_PT_TIMESTAMP = 0
    path_table: dict = {}

    @classmethod
    def reset(cls):
        cls.requested = []
        cls.paths = set()
        cls.hops_map = {}
        cls.path_table = {}

    @staticmethod
    def request_path(dest_hash):
        _FakeTransport.requested.append(dest_hash.hex())

    @staticmethod
    def has_path(dest_hash):
        return dest_hash.hex() in _FakeTransport.paths

    @staticmethod
    def hops_to(dest_hash):
        return _FakeTransport.hops_map.get(dest_hash.hex())

    @staticmethod
    def next_hop_interface(dest_hash):
        return "TCPInterface[Entry Node]"


def _started_lane(monkeypatch):
    """An RnsLane marked started, with RNS.Transport/Identity faked out."""
    from nucleusd.messaging import rns_lane

    _FakeTransport.reset()
    fake_rns = types.ModuleType("RNS")
    fake_rns.Transport = _FakeTransport
    fake_rns.Identity = types.SimpleNamespace(recall=lambda h: object())
    monkeypatch.setitem(sys.modules, "RNS", fake_rns)
    monkeypatch.setitem(sys.modules, "LXMF", types.ModuleType("LXMF"))
    lane = rns_lane.RnsLane(display_name="0042-nucleus")
    lane._started = True
    return lane


def test_request_path_emits_one_request(monkeypatch):
    lane = _started_lane(monkeypatch)
    res = lane.request_path(FIXTURE_LXMF)
    assert res["ok"] is True
    assert _FakeTransport.requested == [FIXTURE_LXMF]


def test_request_path_rejects_bad_hash(monkeypatch):
    lane = _started_lane(monkeypatch)
    res = lane.request_path("nothex")
    assert res["ok"] is False
    assert _FakeTransport.requested == []


def test_path_info_reports_real_interface_from_rnsd(monkeypatch):
    """path_info must read rnsd's OWN path table (over its RPC socket) so it
    reports the ACTUAL interface a path was learned on — any transport, not an
    assumed WiFi "mesh". The lane's client-side table only ever shows the local
    shared-instance socket, so it must not be used."""
    lane = _started_lane(monkeypatch)
    from nucleusd.messaging import rns_lane
    # Real rnsd path_table() reply shape: list of dicts keyed by hex hash. Include
    # an unrelated entry to prove we match on hash, plus the AutoInterfacePeer
    # format rnsd actually emits for a wlan1-learned path.
    monkeypatch.setattr(rns_lane.reti, "path_table", lambda: [
        {"hash": "deadbeef" * 4, "via": "aa", "hops": 9,
         "interface": "TCPInterface[Entry Node/1.2.3.4:4243]", "timestamp": 1.0},
        {"hash": FIXTURE_LXMF, "via": "34dd0a1dd2aae0d5c90c9b6a6879b458", "hops": 1,
         "interface": "AutoInterfacePeer[wlan1/fe80::8ae5:2ce0:5629:f96e]",
         "timestamp": 1700000000.0},
    ])
    info = lane.path_info(FIXTURE_LXMF)
    assert info == {"known": True, "hops": 1,
                    "interface": "AutoInterfacePeer[wlan1/fe80::8ae5:2ce0:5629:f96e]",
                    "updated": 1700000000.0}
    # Reading the path table must never request a path.
    assert _FakeTransport.requested == []


def test_path_info_unknown_when_not_in_rnsd_table(monkeypatch):
    lane = _started_lane(monkeypatch)
    from nucleusd.messaging import rns_lane
    monkeypatch.setattr(rns_lane.reti, "path_table", lambda: [])
    info = lane.path_info(FIXTURE_LXMF)
    assert info == {"known": False, "hops": None, "interface": None, "updated": None}


def test_path_info_unknown_when_rnsd_unreachable(monkeypatch):
    """If the rnsd RPC socket errors, path_info degrades to 'no path', never
    raising — the contact row must still render."""
    lane = _started_lane(monkeypatch)
    from nucleusd.messaging import rns_lane

    def _boom():
        raise rns_lane.reti.ReticulumError("socket down")

    monkeypatch.setattr(rns_lane.reti, "path_table", _boom)
    info = lane.path_info(FIXTURE_LXMF)
    assert info == {"known": False, "hops": None, "interface": None, "updated": None}


def test_send_without_path_refuses_and_emits_nothing(monkeypatch):
    """LPI: a send with no known path must NOT announce or request a path; it
    refuses so LXMF never fires its own path requests on delivery attempts."""
    lane = _started_lane(monkeypatch)
    res = lane.send(FIXTURE_LXMF, "hi")
    assert res["ok"] is False
    assert "no path" in res["error"]
    assert _FakeTransport.requested == []


def test_send_unknown_contact_does_not_request_path(monkeypatch):
    """A send to a contact with no recalled key refuses without emitting."""
    lane = _started_lane(monkeypatch)
    sys.modules["RNS"].Identity = types.SimpleNamespace(recall=lambda h: None)
    res = lane.send(FIXTURE_LXMF, "hi")
    assert res["ok"] is False
    assert _FakeTransport.requested == []
