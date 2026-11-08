"""Meshtastic client: chat store, packet helpers, prefs, geo math.

Pure (no radio, no sockets). cot-bridge — the only process connected to the
radio — WRITES the chat store and the client node file; the API READS them.
Both live in /run (RAM), so all Meshtastic chat history is cleared on every
boot. Only the operator's prefs (favorites / ignored / read marks) persist.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import threading
import time

# /run/nucleus-meshchat is created by cot-bridge.service (RuntimeDirectory=).
RUN_DIR = "/run/nucleus-meshchat"
CHAT_DB_PATH = RUN_DIR + "/chat.db"
CLIENT_NODES_PATH = RUN_DIR + "/nodes.json"
PREFS_PATH = "/var/lib/nucleus/meshclient.json"

PER_CHAT_LIMIT = 500          # messages kept per chat (RAM-backed /run)
BROADCAST_NUM = 0xFFFFFFFF
MAX_CHANNELS = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  pkt_id   INTEGER,
  chat     TEXT NOT NULL,
  sender   INTEGER NOT NULL,
  text     TEXT NOT NULL,
  ts       REAL NOT NULL,
  mine     INTEGER NOT NULL DEFAULT 0,
  reply_id INTEGER,
  state    TEXT,
  UNIQUE (sender, pkt_id)
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages (chat, ts);
CREATE TABLE IF NOT EXISTS reactions (
  pkt_id INTEGER NOT NULL,
  sender INTEGER NOT NULL,
  emoji  TEXT NOT NULL,
  ts     REAL NOT NULL,
  PRIMARY KEY (pkt_id, sender, emoji)
);
"""


# ── ids / chat keys ──────────────────────────────────────────────
# A chat key is "ch<N>" for channel N broadcasts or "!<hex>" for a DM peer.

def node_id(num: int) -> str:
    return f"!{num & 0xFFFFFFFF:08x}"


def parse_node_id(nid: str) -> int:
    """'!a1b2c3d4' -> int. Raises ValueError on anything else."""
    s = str(nid or "")
    if len(s) != 9 or not s.startswith("!"):
        raise ValueError(f"bad node id: {nid!r}")
    return int(s[1:], 16)


def chat_dest(chat: str) -> tuple[int, int]:
    """Chat key -> (destination num, channel index). Raises ValueError."""
    s = str(chat or "")
    if s.startswith("ch") and s[2:].isdigit():
        ch = int(s[2:])
        if 0 <= ch < MAX_CHANNELS:
            return BROADCAST_NUM, ch
        raise ValueError(f"bad channel: {chat!r}")
    return parse_node_id(s), 0


# ── received packets ─────────────────────────────────────────────

def parse_rx_text(packet: dict, my_num: int | None) -> dict | None:
    """Turn a meshtastic-lib TEXT_MESSAGE_APP packet dict into a chat record.

    Returns None for packets that aren't for us (not broadcast, not to my_num)
    or carry no payload. `emoji` true = a reaction to `reply_id`.
    """
    decoded = packet.get("decoded") or {}
    payload = decoded.get("payload")
    frm = packet.get("from")
    if not isinstance(payload, (bytes, bytearray)) or not payload or not isinstance(frm, int):
        return None
    to = packet.get("to", BROADCAST_NUM)
    if to == BROADCAST_NUM:
        chat = f"ch{int(packet.get('channel') or 0)}"
    elif my_num is None or to == my_num:
        chat = node_id(frm)
    else:
        return None
    return {
        "pkt_id": packet.get("id"),
        "chat": chat,
        "sender": frm,
        "text": bytes(payload).decode("utf-8", "replace"),
        "ts": float(packet.get("rxTime") or time.time()),
        "reply_id": decoded.get("replyId") or None,
        "emoji": bool(decoded.get("emoji")),
    }


def forward_to_messaging(rec: dict) -> bool:
    """Only plain primary-channel broadcasts go to nucleus-messaging."""
    return rec["chat"] == "ch0" and not rec["emoji"]


def dm_ack(packet: dict, my_num: int | None) -> tuple[int, str] | None:
    """ROUTING_APP packet -> (request id, 'delivered'|'failed') or None.

    An ACK from our own radio is only the implicit "rebroadcast heard" ack, not
    delivery, so it is ignored; a NAK from our own radio (e.g. MAX_RETRANSMIT)
    is a failure.
    """
    decoded = packet.get("decoded") or {}
    req_id = decoded.get("requestId")
    if not req_id:
        return None
    ok = (decoded.get("routing") or {}).get("errorReason", "NONE") == "NONE"
    if ok and packet.get("from") == my_num:
        return None
    return req_id, ("delivered" if ok else "failed")


def parse_traceroute(decoded: dict, me: int, dest: int) -> dict:
    """RouteDiscovery dict (camelCase, from the lib) -> hop lists + SNR (dB)."""
    rd = decoded.get("traceroute") or {}

    def snrs(key, n):
        vals = rd.get(key) or []
        if len(vals) != n:
            return [None] * n
        return [None if v == -128 else v / 4 for v in vals]

    towards = [me] + list(rd.get("route") or []) + [dest]
    back = [dest] + list(rd.get("routeBack") or []) + [me]
    has_back = "snrBack" in rd
    return {
        "towards": [node_id(n) for n in towards],
        "snr_towards": snrs("snrTowards", len(towards) - 1),
        "back": [node_id(n) for n in back] if has_back else [],
        "snr_back": snrs("snrBack", len(back) - 1) if has_back else [],
    }


# ── node entries (client node file) ──────────────────────────────

def node_entry(num: int, node: dict, user: dict, last_heard: int) -> dict:
    """Flatten a meshtastic-lib node dict into the client node record."""
    pos = node.get("position") or {}
    dm = node.get("deviceMetrics") or {}
    position = None
    if pos.get("latitude") is not None and pos.get("longitude") is not None:
        position = {"lat": pos["latitude"], "lon": pos["longitude"],
                    "alt": pos.get("altitude"), "time": pos.get("time")}
    return {
        "num": num,
        "id": user.get("id") or node_id(num),
        "short_name": user.get("shortName", ""),
        "long_name": user.get("longName", ""),
        "hw_model": user.get("hwModel"),
        "role": user.get("role", "CLIENT"),
        "last_heard": last_heard,
        "snr": node.get("snr"),
        "hops_away": node.get("hopsAway"),
        "position": position,
        "battery": dm.get("batteryLevel"),
        "voltage": dm.get("voltage"),
        "channel_util": dm.get("channelUtilization"),
        "air_util_tx": dm.get("airUtilTx"),
        "uptime": dm.get("uptimeSeconds"),
    }


def distance_bearing(lat1, lon1, lat2, lon2) -> tuple[float, float]:
    """Great-circle distance (m) and initial bearing (deg) from 1 to 2."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dist = 2 * r * math.asin(math.sqrt(a))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return dist, (math.degrees(math.atan2(y, x)) + 360) % 360


# ── chat store (SQLite in /run) ──────────────────────────────────

class ChatStore:
    """Thread-safe SQLite chat store. readonly=True for API readers."""

    def __init__(self, path: str = CHAT_DB_PATH, readonly: bool = False,
                 limit: int = PER_CHAT_LIMIT):
        self.limit = limit
        self._lock = threading.Lock()
        if readonly:
            self._db = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                       check_same_thread=False, timeout=5)
        else:
            self._db = sqlite3.connect(path, check_same_thread=False, timeout=5)
            self._db.executescript(_SCHEMA)
        self._db.row_factory = sqlite3.Row

    def close(self) -> None:
        self._db.close()

    def add(self, chat, sender, text, ts, pkt_id, mine=False,
            reply_id=None, state=None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO messages "
                "(pkt_id, chat, sender, text, ts, mine, reply_id, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pkt_id, chat, sender, text, ts, int(mine), reply_id, state))
            self._db.execute(
                "DELETE FROM messages WHERE chat = ? AND rowid NOT IN "
                "(SELECT rowid FROM messages WHERE chat = ? ORDER BY ts DESC LIMIT ?)",
                (chat, chat, self.limit))

    def react(self, pkt_id, sender, emoji, ts) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO reactions (pkt_id, sender, emoji, ts) "
                "VALUES (?, ?, ?, ?)", (pkt_id, sender, emoji, ts))

    def set_state(self, pkt_id, state) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE messages SET state = ? WHERE pkt_id = ? AND mine = 1",
                (state, pkt_id))

    def chats(self) -> list[dict]:
        """One row per chat: newest message ts + text."""
        with self._lock:
            rows = self._db.execute(
                "SELECT chat, text, MAX(ts) AS last_ts FROM messages GROUP BY chat"
            ).fetchall()
        return [{"chat": r["chat"], "last_ts": r["last_ts"], "last_text": r["text"]}
                for r in rows]

    def unread(self, chat, since, ignored=()) -> int:
        q = "SELECT COUNT(*) FROM messages WHERE chat = ? AND mine = 0 AND ts > ?"
        args = [chat, since]
        if ignored:
            q += f" AND sender NOT IN ({','.join('?' * len(ignored))})"
            args += list(ignored)
        with self._lock:
            return self._db.execute(q, args).fetchone()[0]

    def messages(self, chat, limit=100) -> list[dict]:
        """Newest `limit` messages of a chat, oldest first, with reactions."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM messages WHERE chat = ? ORDER BY ts DESC LIMIT ?",
                (chat, limit)).fetchall()
            msgs = [dict(r) for r in reversed(rows)]
            ids = [m["pkt_id"] for m in msgs if m["pkt_id"] is not None]
            reacts = []
            if ids:
                reacts = self._db.execute(
                    "SELECT pkt_id, sender, emoji FROM reactions WHERE pkt_id IN "
                    f"({','.join('?' * len(ids))}) ORDER BY ts", ids).fetchall()
        by_id: dict = {}
        for r in reacts:
            by_id.setdefault(r["pkt_id"], []).append(
                {"sender": r["sender"], "emoji": r["emoji"]})
        for m in msgs:
            m["reactions"] = by_id.get(m["pkt_id"], [])
        return msgs


# ── operator prefs (persistent, tiny JSON) ───────────────────────

class Prefs:
    """favorites / ignored node ids + per-chat read marks."""

    def __init__(self, path: str = PREFS_PATH):
        self.path = path
        self.data = {"favorites": [], "ignored": [], "read": {}}
        try:
            with open(path) as f:
                loaded = json.load(f)
            for k, default in self.data.items():
                if isinstance(loaded.get(k), type(default)):
                    self.data[k] = loaded[k]
        except Exception:
            pass

    def _save(self) -> None:
        d = os.path.dirname(self.path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def set_flag(self, kind: str, nid: str, on: bool) -> None:
        lst = self.data[kind]
        if on and nid not in lst:
            lst.append(nid)
        elif not on and nid in lst:
            lst.remove(nid)
        else:
            return
        self._save()

    def mark_read(self, chat: str, ts: float) -> None:
        if ts > self.data["read"].get(chat, 0):
            self.data["read"][chat] = ts
            self._save()
