"""Single message store with cross-transport de-duplication.

This is the "single location that both paths deliver to". Every inbound message
— whether it arrived over WiFi multicast or Meshtastic LoRa — is ingested here.
The store keeps ONE logical entry per message and records which transport(s)
delivered it (first-wins for ordering; later copies just add a transport tag).

Pure and unit-testable: no sockets, no threads, no filesystem required (an
optional JSONL path persists history across reboots). All the transport I/O
lives in service.py.

De-dupe key doctrine
--------------------
The LoRa copy of a message is standard Meshtastic text with NO custom id (so
plain Meshtastic radios interoperate). We therefore cannot match copies by an
embedded id. Instead a message is identified by ``(sender, text)`` seen within
``dedupe_window_secs``. The WiFi copy of a Nucleus-originated message carries a
msg_id too, but matching falls back to (sender, text) so a WiFi copy and a LoRa
copy of the same utterance collapse into one entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from typing import Optional


def content_key(sender: str, text: str) -> str:
    """Stable key for a message's *content* (sender + text), ignoring transport.

    Used to collapse the WiFi and LoRa copies of the same utterance.
    """
    h = hashlib.sha256()
    h.update((sender or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((text or "").encode("utf-8"))
    return h.hexdigest()[:16]


class MessageStore:
    """Thread-safe in-memory message log with optional JSONL persistence."""

    def __init__(self, dedupe_window_secs: int = 60, history_limit: int = 500,
                 persist_path: Optional[str] = None):
        self.dedupe_window = dedupe_window_secs
        self.history_limit = history_limit
        self.persist_path = persist_path
        self._lock = threading.Lock()
        self._messages: list[dict] = []      # ordered oldest→newest
        self._recent: dict[str, dict] = {}   # content_key → message (dedupe window)
        if persist_path:
            self._load()

    # ── ingest ──────────────────────────────────────────────────
    def ingest(self, sender: str, text: str, transport: str,
               ts: Optional[float] = None, mine: bool = False) -> tuple[dict, bool]:
        """Add a received/sent message, de-duplicating across transports.

        Returns ``(message, is_new)``. When a matching message already arrived
        within the window, the existing entry is returned with ``transport``
        merged into its ``transports`` list and ``is_new=False``.
        """
        now = ts if ts is not None else time.time()
        key = content_key(sender, text)
        with self._lock:
            self._expire(now)
            existing = self._recent.get(key)
            if existing is not None:
                if transport not in existing["transports"]:
                    existing["transports"].append(transport)
                    self._save()
                return existing, False
            msg = {
                "id": key,
                "sender": sender,
                "text": text,
                "ts": now,
                "transports": [transport],
                "mine": bool(mine),
            }
            self._messages.append(msg)
            self._recent[key] = msg
            if len(self._messages) > self.history_limit:
                drop = self._messages[: len(self._messages) - self.history_limit]
                self._messages = self._messages[-self.history_limit:]
                for d in drop:
                    self._recent.pop(d["id"], None)
            self._save()
            return msg, True

    def _expire(self, now: float) -> None:
        """Drop entries out of the dedupe window from the match index only.

        The messages themselves stay in history; only their eligibility to
        absorb a late duplicate expires.
        """
        stale = [k for k, m in self._recent.items()
                 if now - m["ts"] > self.dedupe_window]
        for k in stale:
            del self._recent[k]

    # ── read ────────────────────────────────────────────────────
    def history(self, since: float = 0.0) -> list[dict]:
        with self._lock:
            return [dict(m) for m in self._messages if m["ts"] > since]

    # ── persistence (JSONL) ─────────────────────────────────────
    def _load(self) -> None:
        try:
            with open(self.persist_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    m = json.loads(line)
                    self._messages.append(m)
                    self._recent[m["id"]] = m
            self._messages = self._messages[-self.history_limit:]
        except OSError:
            pass
        except Exception:
            # Corrupt history must never crash the daemon.
            self._messages = []
            self._recent = {}

    def _save(self) -> None:
        if not self.persist_path:
            return
        try:
            d = os.path.dirname(self.persist_path)
            if d:
                os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d or None, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                for m in self._messages:
                    f.write(json.dumps(m) + "\n")
            os.replace(tmp, self.persist_path)
        except Exception:
            pass
