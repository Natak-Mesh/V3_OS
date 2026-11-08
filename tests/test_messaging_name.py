"""This node's messaging name (messaging.display_name).

One value drives both the WiFi/LoRa chat sender and the Reticulum/LXMF announce
display name. Blank = the hostname. Covers the schema contract, the GET/PUT
/api/v1/messaging/name endpoint (persist + live daemon update), the daemon's
live rename, and that the name really lands in the LXMF announce app_data
(decoded with the installed LXMF, the same way Sideband/NomadNet read it).
"""

import textwrap
import threading

import pytest
from fastapi.testclient import TestClient

from nucleusd import config as cfgio
from nucleusd.api import app
from nucleusd.messaging import router as mrouter
from nucleusd.messaging.rns_store import ConversationStore
from nucleusd.schema import NucleusConfig

client = TestClient(app)

_CONFIG = """
    node:
      id: 7
    mesh:
      subnet_prefix: 10.20.1
      password: pw123456
    ap:
      password: ap123456
    web:
      user: admin
      password: s3cr3t-web-pw
"""


def _cfg(**messaging):
    raw = {"mesh": {"password": "pw123456"}, "ap": {"password": "ap123456"},
           "node": {"id": 42}}
    if messaging:
        raw["messaging"] = messaging
    return NucleusConfig.model_validate(raw)


def _seed(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(_CONFIG))
    monkeypatch.setattr(cfgio, "CONFIG_PATH", p)
    return p


# ── schema ───────────────────────────────────────────────────────
def test_name_defaults_to_hostname():
    cfg = _cfg()
    assert cfg.messaging.display_name is None
    assert cfg.messaging_name == "0042-nucleus"


def test_name_override_and_blank_resets():
    assert _cfg(display_name="  Alpha Base ").messaging_name == "Alpha Base"
    assert _cfg(display_name="   ").messaging.display_name is None
    assert _cfg(display_name="").messaging_name == "0042-nucleus"


def test_name_rejects_control_chars_and_overlong():
    with pytest.raises(Exception):
        _cfg(display_name="bad\nname")
    with pytest.raises(Exception):
        _cfg(display_name="x" * 33)


def test_messaging_daemon_uses_display_name(monkeypatch):
    from nucleusd.messaging import service
    monkeypatch.setattr(cfgio, "load", lambda: _cfg(display_name="Alpha"))
    assert service.load_config()["sender"] == "Alpha"


# ── API: GET/PUT /api/v1/messaging/name ──────────────────────────
def test_put_name_persists_and_live_updates(tmp_path, monkeypatch):
    p = _seed(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(mrouter, "_rpc", lambda req, timeout=2.0: sent.append(req) or {"ok": True})
    r = client.put("/api/v1/messaging/name", json={"name": "Alpha"})
    assert r.status_code == 200
    assert r.json()["name"] == "Alpha" and r.json()["live"] is True
    assert cfgio.load(p).messaging.display_name == "Alpha"
    assert sent == [{"cmd": "set_name", "name": "Alpha"}]
    # Untouched sections survive the rewrite (web password not wiped).
    assert cfgio.load(p).web.password == "s3cr3t-web-pw"
    assert client.get("/api/v1/messaging/name").json()["name"] == "Alpha"


def test_put_blank_name_resets_to_hostname(tmp_path, monkeypatch):
    p = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(mrouter, "_rpc", lambda req, timeout=2.0: {"ok": True})
    client.put("/api/v1/messaging/name", json={"name": "Alpha"})
    r = client.put("/api/v1/messaging/name", json={"name": ""})
    assert r.json()["name"] == "0007-nucleus"
    assert cfgio.load(p).messaging.display_name is None


def test_put_name_saves_even_when_daemon_down(tmp_path, monkeypatch):
    p = _seed(tmp_path, monkeypatch)

    def down(req, timeout=2.0):
        raise mrouter.HTTPException(status_code=503, detail="messaging daemon unavailable")
    monkeypatch.setattr(mrouter, "_rpc", down)
    r = client.put("/api/v1/messaging/name", json={"name": "Alpha"})
    assert r.status_code == 200 and r.json()["live"] is False
    assert cfgio.load(p).messaging.display_name == "Alpha"


def test_put_invalid_name_422_and_not_saved(tmp_path, monkeypatch):
    p = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(mrouter, "_rpc", lambda req, timeout=2.0: {"ok": True})
    r = client.put("/api/v1/messaging/name", json={"name": "a\tb"})
    assert r.status_code == 422
    assert cfgio.load(p).messaging.display_name is None


# ── daemon live rename ───────────────────────────────────────────
class _Lane:
    def __init__(self):
        self.names = []

    def set_display_name(self, name):
        self.names.append(name)


def _svc():
    from nucleusd.messaging.service import MessagingService
    svc = MessagingService.__new__(MessagingService)
    svc.cfg = {"sender": "0042-nucleus"}
    svc.sender = "0042-nucleus"
    svc._rns_lane = _Lane()
    svc.rns_enabled = True
    svc._subs_lock = threading.Lock()
    return svc


def test_daemon_set_name_updates_sender_and_lane():
    svc = _svc()
    assert svc._handle({"cmd": "set_name", "name": " Alpha "}) == {"ok": True, "name": "Alpha"}
    assert svc.sender == "Alpha"
    assert svc._rns_lane.names == ["Alpha"]


def test_daemon_set_name_rejects_empty():
    svc = _svc()
    assert svc._handle({"cmd": "set_name", "name": "  "})["ok"] is False
    assert svc.sender == "0042-nucleus"


# ── the name really goes out in the LXMF announce ────────────────
def test_lane_rename_changes_lxmf_announce_app_data():
    """LXMF builds announce app_data from the delivery destination's
    display_name at announce time. Pin that set_display_name changes what a
    peer decodes, using the real installed LXMF encoder/decoder."""
    LXMF = pytest.importorskip("LXMF")
    from nucleusd.messaging.rns_lane import RnsLane

    class _Dest:
        display_name = "0042-nucleus"
        stamp_cost = None

    dest = _Dest()
    router = LXMF.LXMRouter.__new__(LXMF.LXMRouter)
    router.delivery_destinations = {b"h": dest}

    lane = RnsLane(display_name="0042-nucleus")
    lane._delivery = dest
    lane.set_display_name("Alpha")
    assert lane.display_name == "Alpha"
    app_data = router.get_announce_app_data(b"h")
    assert LXMF.display_name_from_app_data(app_data) == "Alpha"


# ── contact-list preview ─────────────────────────────────────────
def test_conversation_store_latest_per_peer():
    st = ConversationStore()
    st.add("aa", "one", "in", ts=1.0)
    st.add("aa", "two", "out", ts=2.0)
    st.add("bb", "hi", "in", ts=1.5)
    latest = st.latest()
    assert latest["aa"]["text"] == "two" and latest["aa"]["direction"] == "out"
    assert latest["bb"]["text"] == "hi"


def test_daemon_rns_latest_cmd():
    svc = _svc()
    svc._rns_store = ConversationStore()
    svc._rns_store.add("aa", "yo", "in", ts=5.0)
    r = svc._handle({"cmd": "rns_latest"})
    assert r["ok"] and r["latest"]["aa"]["text"] == "yo"
