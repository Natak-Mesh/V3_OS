"""Meshtastic client helpers (nucleusd.meshtastic.meshchat). Off-box."""

import json

import pytest

from nucleusd.meshtastic import meshchat
from tests.meshtastic_fixtures import (
    ACK, CH1_BROADCAST, DM_REACTION, DM_TO_OTHER, IMPLICIT_ACK_LOCAL, ME,
    NAK_LOCAL, PEER, PRIMARY_BROADCAST, TRACEROUTE,
)


# ── packet parsing / routing ─────────────────────────────────────

def test_secondary_channel_broadcast():
    rec = meshchat.parse_rx_text(CH1_BROADCAST, ME)
    assert rec["chat"] == "ch1" and rec["text"] == "hello ch1"
    assert rec["pkt_id"] == 1234567 and rec["ts"] == 1791650000
    assert not meshchat.forward_to_messaging(rec)


def test_primary_broadcast_is_forwarded_to_messaging():
    rec = meshchat.parse_rx_text(PRIMARY_BROADCAST, ME)
    assert rec["chat"] == "ch0"
    assert meshchat.forward_to_messaging(rec)


def test_dm_reaction_not_forwarded():
    rec = meshchat.parse_rx_text(DM_REACTION, ME)
    assert rec["chat"] == "!21d4c2cc"
    assert rec["emoji"] and rec["reply_id"] == 999 and rec["text"] == "👍"
    assert not meshchat.forward_to_messaging(rec)


def test_dm_for_another_node_ignored():
    assert meshchat.parse_rx_text(DM_TO_OTHER, ME) is None


def test_dm_ack_states():
    assert meshchat.dm_ack(ACK, ME) == (777, "delivered")
    assert meshchat.dm_ack(NAK_LOCAL, ME) == (777, "failed")
    # Our own radio's implicit ack is not a delivery confirmation.
    assert meshchat.dm_ack(IMPLICIT_ACK_LOCAL, ME) is None


def test_traceroute_parse():
    r = meshchat.parse_traceroute(TRACEROUTE, ME, PEER)
    assert r["towards"] == ["!3007771f", "!67c599e4", "!21d4c2cc"]
    assert r["snr_towards"] == [10.0, 5.5]
    assert r["back"] == ["!21d4c2cc", "!67c599e4", "!3007771f"]
    assert r["snr_back"] == [7.5, None]


def test_chat_dest():
    assert meshchat.chat_dest("ch2") == (meshchat.BROADCAST_NUM, 2)
    assert meshchat.chat_dest("!21d4c2cc") == (PEER, 0)
    for bad in ("ch9", "x", "!zz", ""):
        with pytest.raises(ValueError):
            meshchat.chat_dest(bad)


def test_distance_bearing_due_north():
    d, b = meshchat.distance_bearing(45.0, -122.0, 45.01, -122.0)
    assert round(d) == 1112 and round(b) == 0


# ── chat store ───────────────────────────────────────────────────

def test_store_messages_reactions_state(tmp_path):
    s = meshchat.ChatStore(str(tmp_path / "chat.db"))
    s.add("!21d4c2cc", ME, "hi", 10.0, 777, mine=True, state="sending")
    s.add("!21d4c2cc", PEER, "yo", 11.0, 888, reply_id=777)
    s.add("!21d4c2cc", PEER, "yo", 11.0, 888)      # duplicate packet ignored
    s.react(777, PEER, "👍", 12.0)
    s.set_state(777, "delivered")
    msgs = s.messages("!21d4c2cc")
    assert [m["text"] for m in msgs] == ["hi", "yo"]
    assert msgs[0]["state"] == "delivered"
    assert msgs[0]["reactions"] == [{"sender": PEER, "emoji": "👍"}]
    assert msgs[1]["reply_id"] == 777
    assert s.unread("!21d4c2cc", 10.5) == 1
    assert s.unread("!21d4c2cc", 10.5, ignored=[PEER]) == 0
    assert s.chats() == [{"chat": "!21d4c2cc", "last_ts": 11.0, "last_text": "yo"}]


def test_store_caps_messages_per_chat(tmp_path):
    s = meshchat.ChatStore(str(tmp_path / "chat.db"), limit=3)
    for i in range(5):
        s.add("ch0", PEER, f"m{i}", float(i), i)
    assert [m["text"] for m in s.messages("ch0")] == ["m2", "m3", "m4"]


def test_readonly_store_sees_writer(tmp_path):
    path = str(tmp_path / "chat.db")
    w = meshchat.ChatStore(path)
    w.add("ch0", PEER, "hello", 1.0, 1)
    r = meshchat.ChatStore(path, readonly=True)
    assert r.messages("ch0")[0]["text"] == "hello"


def test_chat_store_lives_in_run():
    """All Meshtastic chat history is RAM-backed: cleared on every boot."""
    assert meshchat.CHAT_DB_PATH.startswith("/run/")
    assert meshchat.CLIENT_NODES_PATH.startswith("/run/")
    assert meshchat.PREFS_PATH.startswith("/var/lib/nucleus/")


# ── prefs ────────────────────────────────────────────────────────

def test_prefs_roundtrip(tmp_path):
    path = str(tmp_path / "meshclient.json")
    p = meshchat.Prefs(path)
    p.set_flag("favorites", "!21d4c2cc", True)
    p.set_flag("ignored", "!67c599e4", True)
    p.mark_read("ch0", 5.0)
    p.mark_read("ch0", 3.0)          # never moves backwards
    q = meshchat.Prefs(path)
    assert q.data == {"favorites": ["!21d4c2cc"], "ignored": ["!67c599e4"],
                      "read": {"ch0": 5.0}}
    q.set_flag("favorites", "!21d4c2cc", False)
    assert json.load(open(path))["favorites"] == []


def test_prefs_corrupt_file_uses_defaults(tmp_path):
    path = tmp_path / "meshclient.json"
    path.write_text("{not json")
    assert meshchat.Prefs(str(path)).data["favorites"] == []


# ── messaging history location (regression) ─────────────────────

def test_messaging_history_cleared_on_boot():
    """WiFi+LoRa messaging history lives in /run (RAM) and the unit creates it."""
    from pathlib import Path
    from nucleusd.messaging import service
    assert service.PERSIST_PATH == "/run/nucleus-messaging/messages.jsonl"
    unit = (Path(__file__).resolve().parents[1] /
            "system/systemd/nucleus-messaging.service").read_text()
    assert "RuntimeDirectory=nucleus-messaging" in unit
    assert "RuntimeDirectoryPreserve=yes" in unit


def test_cot_bridge_unit_creates_chat_dir():
    from pathlib import Path
    unit = (Path(__file__).resolve().parents[1] /
            "system/systemd/cot-bridge.service").read_text()
    assert "RuntimeDirectory=nucleus-meshchat" in unit
    assert "RuntimeDirectoryPreserve=yes" in unit
