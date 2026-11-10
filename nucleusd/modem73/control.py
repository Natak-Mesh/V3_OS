"""modem73 control-port client (TCP JSON, 4-byte big-endian length prefix).

Two access paths, both to 127.0.0.1:8073 (modem73 binds it to localhost):

* ``request()`` — one-shot command on a short-lived connection (set_config,
  tx, ...). Pushed events that arrive before the reply are skipped.
* ``Monitor`` — ONE shared, long-lived connection that caches what the live
  page shows: latest get_status (polled every STATUS_POLL_S), get_config
  (refreshed by the pushed ``config_changed`` event) and the last MAX_FRAMES
  decoded frames (pushed ``rx_frame`` events). modem73 broadcasts events to
  every client, so one connection fans out to any number of browsers.

The monitor only holds the connection while someone is looking (``touch()``
within IDLE_S); on reconnect it catches up from modem73's own frame history
(``rx_frame_history``), so frames decoded while idle are not lost.
"""

from __future__ import annotations

import json
import select
import socket
import struct
import threading
import time
from collections import deque

from . import logtail

CONTROL_ADDR = ("127.0.0.1", 8073)
TIMEOUT = 3.0
# modem73 drops a client that sends > 1 MiB; replies are far smaller.
MAX_MSG = 1024 * 1024
STATUS_POLL_S = 2.0
RECONNECT_S = 3.0
IDLE_S = 120.0
MAX_FRAMES = 64


class Modem73Error(Exception):
    """modem73 control port unreachable or returned an error."""


def encode(obj: dict) -> bytes:
    b = json.dumps(obj).encode("utf-8")
    return struct.pack(">I", len(b)) + b


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise Modem73Error("control port closed the connection")
        buf += chunk
    return buf


def read_msg(sock: socket.socket) -> dict:
    (n,) = struct.unpack(">I", _read_exact(sock, 4))
    if n > MAX_MSG:
        raise Modem73Error(f"control message too large ({n} bytes)")
    return json.loads(_read_exact(sock, n).decode("utf-8"))


def request(cmd: str, timeout: float = TIMEOUT, **params) -> dict:
    """Send one command and return its reply (events before it are skipped)."""
    try:
        with socket.create_connection(CONTROL_ADDR, timeout=timeout) as s:
            s.sendall(encode({"cmd": cmd, **params}))
            while True:
                msg = read_msg(s)
                if "event" not in msg:
                    return msg
    except (OSError, ValueError) as e:
        raise Modem73Error(f"modem73 control port unavailable: {e}") from e


def _merge_frames(frames: deque, new: list[dict]) -> None:
    """Merge decoded-frame dicts into `frames`, oldest→newest, deduped.

    Keyed by decode `time` (not `seq`, which restarts at 1 with modem73), so a
    history catch-up after a modem73 restart never duplicates or drops frames.
    """
    merged = list(frames)
    seen = {f.get("time") for f in merged}
    for f in new:
        if f.get("time") in seen:
            continue
        merged.append({k: v for k, v in f.items() if k not in ("event", "age_s")})
        seen.add(f.get("time"))
    merged.sort(key=lambda f: f.get("time") or 0)
    frames.clear()
    frames.extend(merged[-frames.maxlen:])


class Monitor:
    """Shared control-port connection + cache. Thread-safe snapshot()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False
        self._interest = 0.0
        self.online = False
        self.error: str | None = "not connected yet"
        self.status: dict | None = None
        self.status_ts = 0.0
        self.config: dict | None = None
        self.frames: deque = deque(maxlen=MAX_FRAMES)
        # Decoder detail the control port lacks (failed attempts, CFO/SFO, channel dB).
        self.log = logtail.Parser()
        self._tail = logtail.LogTail()
        # Bumped on every cache change; the WebSocket pushes when it moves.
        self.version = 0

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._loop, name="modem73-monitor", daemon=True).start()

    def touch(self) -> None:
        """Mark live interest; keeps (or opens) the shared connection."""
        self._interest = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "online": self.online,
                "error": self.error,
                "status": dict(self.status) if self.status else None,
                "status_ts": self.status_ts,
                "config": dict(self.config) if self.config else None,
                "frames": list(reversed(self.frames)),  # newest first
                "attempts": self.log.view(),
                "channel_db": self.log.channel_db,
                "channel_ts": self.log.channel_ts,
                "version": self.version,
            }

    def poll_log(self) -> None:
        """Fold new modem73 log lines into the cache."""
        lines, backfill = self._tail.read()
        now = None if backfill else time.time()
        with self._lock:
            if sum(self.log.feed(ln, now) for ln in lines):
                self.version += 1

    def handle(self, msg: dict, pending: str | None) -> None:
        """Fold one control-port message into the cache.

        `pending` is the command this message answers (None for a pushed event).
        Pure apart from the lock — unit-tested with captured real messages.
        """
        with self._lock:
            ev = msg.get("event")
            if ev == "rx_frame":
                _merge_frames(self.frames, [msg])
            elif ev == "config_changed":
                if not isinstance(msg.get("config"), dict):
                    return
                self.config = msg["config"]
            elif ev is not None:
                return
            elif pending == "get_status" and msg.get("ok"):
                self.status = {k: v for k, v in msg.items() if k != "ok"}
                self.status_ts = time.time()
            elif pending == "get_config" and msg.get("ok"):
                self.config = {k: v for k, v in msg.items() if k != "ok"}
            elif pending == "rx_frame_history" and msg.get("ok"):
                _merge_frames(self.frames, msg.get("frames") or [])
            else:
                return
            self.version += 1

    def _set_link(self, online: bool, error: str | None) -> None:
        with self._lock:
            if (self.online, self.error) != (online, error):
                self.online, self.error = online, error
                self.version += 1

    def _loop(self) -> None:
        while True:
            if time.time() - self._interest > IDLE_S:
                time.sleep(1.0)
                continue
            try:
                self._session()
            except Exception as e:  # never let one bad session kill the monitor
                self._set_link(False, f"modem73 control port unavailable: {e}")
            time.sleep(RECONNECT_S)

    def _session(self) -> None:
        with socket.create_connection(CONTROL_ADDR, timeout=TIMEOUT) as s:
            pending: deque = deque()

            def send(cmd: str, **params) -> None:
                s.sendall(encode({"cmd": cmd, **params}))
                pending.append(cmd)

            send("get_config")
            send("rx_frame_history", limit=MAX_FRAMES, since_seq=0)
            send("get_status")
            next_poll = time.time() + STATUS_POLL_S
            self._set_link(True, None)
            while time.time() - self._interest <= IDLE_S:
                self.poll_log()
                now = time.time()
                if now >= next_poll:
                    send("get_status")
                    next_poll = now + STATUS_POLL_S
                ready, _, _ = select.select([s], [], [], 0.5)
                if not ready:
                    continue
                msg = read_msg(s)
                cmd = None if "event" in msg else (pending.popleft() if pending else None)
                self.handle(msg, cmd)
        self._set_link(False, "idle")


_monitor: Monitor | None = None
_monitor_lock = threading.Lock()


def monitor() -> Monitor:
    """The process-wide Monitor, started on first use."""
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = Monitor()
            _monitor.start()
        return _monitor
