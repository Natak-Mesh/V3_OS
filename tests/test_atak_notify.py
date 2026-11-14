"""ATAK node notifications: SA discovery, GeoChat, unicast delivery, triggers."""

import socket
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from nucleusd import status
from nucleusd.messaging import atak_notify as an
from tests import atak_fixtures as fx

SUBNET, OWN = "10.20.42.0/24", "10.20.42.1"


def _neigh(dump: str):
    buf = dump.encode()
    return status._parse_neighbours(buf) if status._dump_complete(buf) else None


def _notifier(sent, **over):
    kw = dict(node_id=42, callsign="0042-nucleus", br_lan_ip=OWN, br_lan_subnet=SUBNET,
              link_loss=True, link_ifaces=["wlan1", "halow0"], link_hold_secs=30,
              rns_message=True, device_stale_secs=120,
              send_fn=lambda ip, port, proto, data: sent.append((ip, port, proto, data)))
    kw.update(over)
    return an.AtakNotifier(**kw)


def _remarks(data: bytes) -> str:
    return ET.fromstring(data).find("detail/remarks").text


# ── SA parsing / discovery ─────────────────────────────────────────
def test_parse_real_v1_sa():
    assert an.parse_sa(fx.SA_V1) == {"uid": fx.SA_UID, "type": "a-f-G-U-C",
                                     "endpoint": "10.20.42.55:4242:tcp",
                                     "callsign": fx.SA_CALLSIGN}


def test_parse_xml_sa_and_garbage():
    assert an.parse_sa(fx.SA_XML_UDP)["endpoint"] == "10.20.42.55:4242:udp"
    assert an.parse_sa(fx.SA_V1[:60]) is None          # truncated protobuf
    assert an.parse_sa(b"<event") is None               # broken XML
    assert an.parse_sa(b"") is None


@pytest.mark.parametrize("ep,expect", [
    ("10.20.42.55:4242:tcp", ("10.20.42.55", 4242, "tcp")),
    ("10.20.42.55:4242:udp", ("10.20.42.55", 17012, "udp")),
    ("0.0.0.0:4242:srctcp", ("10.20.42.55", 4242, "tcp")),
    ("*:-1:stcp", None),
    ("10.20.42.55:0:tcp", None),
    ("10.20.42.55:x:tcp", None),
    ("", None),
])
def test_resolve_endpoint(ep, expect):
    assert an.resolve_endpoint(ep, "10.20.42.55") == expect


def test_device_discovered_from_real_sa():
    t = an.DeviceTable(SUBNET, OWN, 120)
    d = t.observe(fx.SA_V1, fx.SA_SRC, 1000.0)
    assert (d["uid"], d["callsign"], d["ip"], d["port"], d["proto"]) == \
        (fx.SA_UID, "0033", "10.20.42.55", 4242, "tcp")


@pytest.mark.parametrize("src", ["10.20.1.47", "10.20.47.55", OWN, "10.0.0.5"])
def test_non_local_or_own_sources_ignored(src):
    # Mesh peers, other nodes' EUDs, the node's own re-injected CoT, eth0.
    t = an.DeviceTable(SUBNET, OWN, 120)
    assert t.observe(fx.SA_V1, src, 1000.0) is None
    assert t.fresh(1000.0) == []


def test_device_goes_stale_and_address_change_tracked():
    t = an.DeviceTable(SUBNET, OWN, 120)
    t.observe(fx.SA_V1, fx.SA_SRC, 1000.0)
    t.observe(fx.SA_XML_UDP, fx.SA_SRC, 1050.0)
    [d] = t.fresh(1100.0)
    assert (d["port"], d["proto"]) == (17012, "udp")
    assert t.fresh(1171.0) == []


# ── GeoChat ────────────────────────────────────────────────────────
def test_geochat_layout_and_escaping():
    now = datetime(2026, 11, 14, 9, 0, 0, tzinfo=timezone.utc)
    raw = an.build_geochat("NUCLEUS-0042", "0042-nucleus", fx.SA_UID,
                           'a <b> & "c"', now=now, msg_id="m1")
    ev = ET.fromstring(raw)
    assert ev.get("type") == "b-t-f"
    assert ev.get("uid") == f"GeoChat.NUCLEUS-0042.{fx.SA_UID}.m1"
    assert ev.get("stale") == "2026-11-15T09:00:00.000Z"
    chat = ev.find("detail/__chat")
    assert chat.get("id") == "NUCLEUS-0042" and chat.get("chatroom") == "0042-nucleus"
    assert chat.get("senderCallsign") == "0042-nucleus" and chat.get("messageId") == "m1"
    grp = chat.find("chatgrp")
    assert (grp.get("uid0"), grp.get("uid1"), grp.get("id")) == \
        ("NUCLEUS-0042", fx.SA_UID, "NUCLEUS-0042")
    rem = ev.find("detail/remarks")
    assert rem.text == 'a <b> & "c"' and rem.get("to") == fx.SA_UID
    assert b"<b>" not in raw


# ── delivery ───────────────────────────────────────────────────────
def test_no_device_drops_without_sending():
    sent = []
    n = _notifier(sent)
    assert n.deliver("test", "x", now=1000.0) == 0
    assert sent == [] and n.stats["dropped_no_device"] == 1


def test_delivers_unicast_to_discovered_endpoint():
    sent = []
    n = _notifier(sent)
    n.devices.observe(fx.SA_V1, fx.SA_SRC, 1000.0)
    assert n.deliver("test", "hello", now=1001.0) == 1
    ip, port, proto, data = sent[0]
    assert (ip, port, proto) == ("10.20.42.55", 4242, "tcp")
    assert _remarks(data) == "hello"


def test_send_failure_counted_not_retried():
    def boom(*a):
        raise ConnectionRefusedError("refused")
    n = _notifier([], send_fn=boom)
    n.devices.observe(fx.SA_V1, fx.SA_SRC, 1000.0)
    assert n.deliver("test", "x", now=1001.0) == 0
    assert n.stats["failed"] == 1 and "refused" in n.last_error


def test_expired_queue_entry_dropped():
    sent = []
    n = _notifier(sent)
    n.devices.observe(fx.SA_V1, fx.SA_SRC, 1000.0)
    n.notify("test", "late", now=1000.0)
    assert n.process_one(timeout=0, now=1000.0 + an.QUEUE_EXPIRY_SECS + 1)
    assert sent == [] and n.stats["dropped_expired"] == 1


@pytest.mark.parametrize("ip", ["239.2.3.1", "224.10.10.1", "10.20.42.255", "0.0.0.0"])
def test_send_unicast_refuses_non_unicast(ip):
    with pytest.raises(OSError):
        an.send_unicast(ip, 4242, "tcp", b"x")


def test_send_unicast_tcp_real_socket():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = []

    def accept():
        c, _ = srv.accept()
        got.append(c.recv(65535))
        c.close()
    t = threading.Thread(target=accept)
    t.start()
    an.send_unicast("127.0.0.1", srv.getsockname()[1], "tcp", b"<event/>")
    t.join(2)
    srv.close()
    assert got == [b"<event/>"]


# ── babeld read handling ───────────────────────────────────────────
def test_truncated_dump_is_failure_not_empty():
    assert _neigh(fx.BABEL_DUMP_TRUNCATED) is None
    assert _neigh("") is None
    assert [n["if"] for n in _neigh(fx.BABEL_DUMP_BOTH)] == ["wlan1", "eth0"]


def test_babel_neighbours_checked_on_refused_socket(monkeypatch):
    monkeypatch.setattr(status, "_babel_dump", lambda *a, **k: b"")
    assert status.babel_neighbours_checked() is None
    assert status.babel_neighbours() == []



# ── link loss ──────────────────────────────────────────────────────
def _tracker():
    return an.LinkTracker(["wlan1", "halow0"], hold_secs=30)


def test_baseline_silent_then_wlan_loss_reported_while_eth_up():
    t = _tracker()
    assert t.update(_neigh(fx.BABEL_DUMP_BOTH), 0) == []
    assert t.update(_neigh(fx.BABEL_DUMP_ETH_ONLY), 5) == []      # inside hold
    [ev] = t.update(_neigh(fx.BABEL_DUMP_ETH_ONLY), 36)
    assert ev == {"event": "lost", "iface": "wlan1", "node": "0047-nucleus (10.20.1.47)"}
    assert t.update(_neigh(fx.BABEL_DUMP_ETH_ONLY), 60) == []     # no repeat
    assert an.link_event_text(ev) == \
        "Wi-Fi mesh link LOST: 0047-nucleus (10.20.1.47) on wlan1"


def test_infinite_cost_counts_as_lost():
    t = _tracker()
    t.update(_neigh(fx.BABEL_DUMP_BOTH), 0)
    t.update(_neigh(fx.BABEL_DUMP_WLAN_INF), 5)
    assert [e["event"] for e in t.update(_neigh(fx.BABEL_DUMP_WLAN_INF), 40)] == ["lost"]


def test_flap_within_hold_is_silent():
    t = _tracker()
    t.update(_neigh(fx.BABEL_DUMP_BOTH), 0)
    for ts in (5, 15, 25):
        assert t.update(_neigh(fx.BABEL_DUMP_ETH_ONLY), ts) == []
        assert t.update(_neigh(fx.BABEL_DUMP_BOTH), ts + 5) == []
    assert t.update(_neigh(fx.BABEL_DUMP_BOTH), 100) == []


def test_restored_only_after_reported_loss():
    t = _tracker()
    t.update(_neigh(fx.BABEL_DUMP_BOTH), 0)
    t.update(_neigh(fx.BABEL_DUMP_ETH_ONLY), 5)
    t.update(_neigh(fx.BABEL_DUMP_ETH_ONLY), 40)
    assert t.update(_neigh(fx.BABEL_DUMP_BOTH), 50) == []
    [ev] = t.update(_neigh(fx.BABEL_DUMP_BOTH), 85)
    assert ev["event"] == "restored" and ev["iface"] == "wlan1"


def test_link_absent_at_startup_never_reported():
    t = _tracker()
    t.update(_neigh(fx.BABEL_DUMP_ETH_ONLY), 0)
    assert t.update(_neigh(fx.BABEL_DUMP_ETH_ONLY), 100) == []
    assert t.update(_neigh(fx.BABEL_DUMP_BOTH), 200) == []   # new link: silent


def test_failed_reads_never_report_loss():
    t = _tracker()
    t.update(_neigh(fx.BABEL_DUMP_BOTH), 0)
    for ts in range(5, 300, 5):
        assert t.update(_neigh(fx.BABEL_DUMP_TRUNCATED), ts) == []
    assert t.update(_neigh(fx.BABEL_DUMP_BOTH), 305) == []


def test_eth0_ignored_unless_configured():
    gone = fx.BABEL_DUMP_BOTH.replace(fx.ETH_NBR, "")
    t = _tracker()
    t.update(_neigh(fx.BABEL_DUMP_BOTH), 0)
    t.update(_neigh(gone), 5)
    assert t.update(_neigh(gone), 40) == []
    t = an.LinkTracker(["eth0"], hold_secs=30)
    t.update(_neigh(fx.BABEL_DUMP_BOTH), 0)
    t.update(_neigh(gone), 5)
    assert [e["iface"] for e in t.update(_neigh(gone), 40)] == ["eth0"]


def test_poll_links_queues_and_counts_read_failures():
    sent = []
    dumps = iter([fx.BABEL_DUMP_BOTH, fx.BABEL_DUMP_TRUNCATED,
                  fx.BABEL_DUMP_ETH_ONLY, fx.BABEL_DUMP_ETH_ONLY])
    n = _notifier(sent, neighbours_fn=lambda: _neigh(next(dumps)))
    n.devices.observe(fx.SA_V1, fx.SA_SRC, 0.0)
    for ts in (0, 5, 10, 45):
        n.poll_links(now=ts)
    assert n.stats["babel_read_failures"] == 1
    assert n.process_one(timeout=0, now=45)
    assert "LOST" in _remarks(sent[0][3])



# ── Reticulum trigger (via the messaging service) ──────────────────
PEER = "11efea534593c763cd173f7d62123bff"


def _svc(atak):
    from nucleusd.messaging.rns_contacts import ContactStore
    from nucleusd.messaging.rns_store import ConversationStore
    from nucleusd.messaging.service import MessagingService
    svc = MessagingService.__new__(MessagingService)
    svc.rns_enabled = True
    svc._rns_store = ConversationStore(history_limit=50)
    svc._rns_contacts = ContactStore()
    svc._subs, svc._subs_lock = {}, threading.Lock()
    svc._notify_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    svc._rns_state_lock = threading.Lock()
    svc._rns_early_state = {}
    svc._atak = atak
    return svc


def test_inbound_lxmf_notifies_sender_only_never_text():
    sent = []
    n = _notifier(sent)
    n.devices.observe(fx.SA_V1, fx.SA_SRC, time.time())
    svc = _svc(n)
    svc._rns_contacts.add_hash(PEER, name="Bravo")
    svc._on_rns_message(PEER, "SECRET grid 18S UJ 123 456", 1000.0)
    assert n.process_one(timeout=0)
    data = sent[0][3]
    assert _remarks(data) == "Reticulum message from Bravo"
    assert b"SECRET" not in data


def test_unknown_sender_uses_short_hash():
    n = _notifier([])
    _svc(n)._on_rns_message(PEER, "hi", 1000.0)
    assert n._q.get_nowait()[2] == f"Reticulum message from {PEER[:10]}"


def test_outbound_and_state_updates_do_not_notify():
    class Lane:
        started = True

        def send(self, dest, text):
            return {"ok": True, "msg_id": "abc", "state": "outbound"}
    n = _notifier([])
    svc = _svc(n)
    svc._rns_lane = Lane()
    svc._rns_send(PEER, "outgoing")
    svc._on_rns_state("abc", "delivered")
    assert n.stats["queued"] == 0


def test_rns_trigger_respects_config_off():
    n = _notifier([], rns_message=False)
    _svc(n)._on_rns_message(PEER, "hi", 1000.0)
    assert n.stats["queued"] == 0


def test_notify_failure_never_breaks_intake():
    class Broken:
        def on_rns_message(self, name):
            raise RuntimeError("boom")
    svc = _svc(Broken())
    svc._on_rns_message(PEER, "hi", 1000.0)
    assert svc._rns_store.history(PEER)[0]["text"] == "hi"


# ── schema / service wiring ────────────────────────────────────────
BASE = {"node": {"id": 42}, "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"}}


def test_schema_defaults_and_validation():
    from pydantic import ValidationError
    from nucleusd.schema import NucleusConfig
    a = NucleusConfig.model_validate(BASE).messaging.atak_notify
    assert (a.enabled, a.link_loss, a.rns_message) == (True, True, True)
    assert a.link_ifaces == ["wlan1", "halow0"] and a.link_hold_secs == 30
    with pytest.raises(ValidationError):
        NucleusConfig.model_validate(
            {**BASE, "messaging": {"atak_notify": {"link_ifaces": ["br-lan"]}}})


def test_build_notifier_from_config(tmp_path, monkeypatch):
    import yaml
    from nucleusd.messaging import service
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(BASE))
    monkeypatch.setenv("NUCLEUS_CONFIG", str(p))
    from nucleusd import config
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    n = service.build_atak_notifier(service.load_config())
    assert (n.sender_uid, n.callsign, n.br_lan_ip) == \
        ("NUCLEUS-0042", "0042 Nucleus notifications", "10.20.42.1")
    off = {**BASE, "messaging": {"atak_notify": {"enabled": False}}}
    p.write_text(yaml.safe_dump(off))
    assert service.build_atak_notifier(service.load_config()) is None

