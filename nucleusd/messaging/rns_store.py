"""Per-peer direct-message store for the Reticulum/LXMF lane.

Deliberately separate from ``store.MessageStore`` (the shared WiFi+LoRa broadcast
log). LXMF messages are addressed to a single node, so they are kept as distinct
per-peer conversations keyed by the peer's lxmf.delivery hash rather than fanned
into one deduped broadcast stream.

Pure and thread-safe with optional JSONL persistence, mirroring MessageStore so
the service can persist DM history across restarts. No sockets/threads here.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Optional


class ConversationStore:
    """Thread-safe per-peer message log (in-memory + optional JSONL)."""

    def __init__(self, history_limit: int = 500, persist_path: Optional[str] = None):
        self.history_limit = history_limit
        self.persist_path = persist_path
        self._lock = threading.Lock()
        # peer_hash -> ordered list of messages (oldest→newest)
        self._convs: dict[str, list[dict]] = {}
        if persist_path:
            self._load()

    def add(self, peer: str, text: str, direction: str,
            ts: Optional[float] = None, state: str = "") -> dict:
        """Append a message to ``peer``'s conversation and return it.

        ``direction`` is "in" or "out"; ``state`` tracks outbound delivery
        (e.g. "outbound", "delivered", "failed"). Inbound messages leave it "".
        """
        now = ts if ts is not None else time.time()
        msg = {
            "peer": peer,
            "text": text,
            "ts": now,
            "direction": direction,
            "state": state,
        }
        with self._lock:
            conv = self._convs.setdefault(peer, [])
            conv.append(msg)
            conv.sort(key=lambda m: m["ts"])
            if len(conv) > self.history_limit:
                del conv[: len(conv) - self.history_limit]
            self._save()
        return dict(msg)

    def history(self, peer: Optional[str] = None, since: float = 0.0) -> list[dict]:
        """Return messages for one peer, or all peers merged, after ``since``."""
        with self._lock:
            if peer is not None:
                return [dict(m) for m in self._convs.get(peer, []) if m["ts"] > since]
            out: list[dict] = []
            for conv in self._convs.values():
                out.extend(dict(m) for m in conv if m["ts"] > since)
            out.sort(key=lambda m: m["ts"])
            return out

    def peers(self) -> list[str]:
        with self._lock:
            return list(self._convs.keys())

    # ── persistence (JSONL, one message per line) ────────────────
    def _load(self) -> None:
        try:
            with open(self.persist_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    m = json.loads(line)
                    self._convs.setdefault(m["peer"], []).append(m)
            for conv in self._convs.values():
                conv.sort(key=lambda m: m["ts"])
                if len(conv) > self.history_limit:
                    del conv[: len(conv) - self.history_limit]
        except OSError:
            pass
        except Exception:
            self._convs = {}

    def _save(self) -> None:
        if not self.persist_path:
            return
        try:
            d = os.path.dirname(self.persist_path)
            if d:
                os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d or None, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                for conv in self._convs.values():
                    for m in conv:
                        f.write(json.dumps(m) + "\n")
            os.replace(tmp, self.persist_path)
        except Exception:
            pass
