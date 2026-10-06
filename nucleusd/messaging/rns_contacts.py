"""Persistent contact book for the Reticulum/LXMF direct-message lane.

Replaces the old announce-driven ``PeerTable`` (which was in-memory and rebuilt
from flooded ``nucleus.node`` announces). Contacts are now explicit: a node is
messageable only once its contact card has been imported (over the WiFi mesh,
pasted as a link, or scanned from a QR), or once it has messaged us (inbound
senders are auto-added — if they have our address they got it on purpose).

Each contact stores the peer's lxmf.delivery hash (the key everything is keyed
on), its 64-byte identity public key (so the lane can reconstruct the recipient
identity and send with no announce/path-table dependency), a display name/id, the
``source`` it came from (``mesh`` / ``link`` / ``qr`` / ``inbound``) and when it
was added. Pure and thread-safe with atomic JSON persistence — no sockets, no RNS
import — so it is fully unit-testable off-box. The live lane feeds/reads it; the
service exposes it over the control socket.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Optional

from . import rns_proto as proto


class ContactStore:
    """Thread-safe contact book keyed by lxmf.delivery hash (hex), JSON-backed."""

    def __init__(self, persist_path: Optional[str] = None):
        self.persist_path = persist_path
        self._lock = threading.Lock()
        self._contacts: dict[str, dict] = {}
        if persist_path:
            self._load()

    def add_card(self, card: dict, source: str = "link",
                 ts: Optional[float] = None) -> dict:
        """Validate + store a contact card. Returns the stored contact.

        The card's address MUST match its key (``proto.verify_card``) or this
        raises ValueError — a card can't claim an address it has no key for.
        Re-importing an existing contact refreshes its fields/key but keeps the
        original ``added`` time and does not downgrade a real name to a blank.
        """
        if not proto.verify_card(card):
            raise ValueError("contact card failed verification (address/key mismatch)")
        now = ts if ts is not None else time.time()
        key = str(card["lxmf_hash"]).lower()
        with self._lock:
            existing = self._contacts.get(key)
            added = existing["added"] if existing else now
            name = str(card.get("name") or "")
            if not name and existing:
                name = existing.get("name", "")
            contact = {
                "lxmf_hash": key,
                "pubkey": str(card["pubkey"]).lower(),
                "name": name,
                "id": card.get("id"),
                "source": source,
                "added": added,
                "last_seen": existing.get("last_seen") if existing else None,
            }
            self._contacts[key] = contact
            self._save()
        return dict(contact)

    def add_inbound(self, lxmf_hash: str, pubkey: Optional[str],
                    name: str = "", ts: Optional[float] = None) -> Optional[dict]:
        """Auto-add/refresh a contact from an inbound message.

        Called when a message arrives from a peer we don't have yet. If we have
        the sender's public key (from the signed message or ``Identity.recall``)
        the contact is fully usable for replies; if not, it is stored keyless and
        upgraded later once the key is learned. ``last_seen`` is always bumped.
        Returns the stored contact, or None if ``lxmf_hash`` is empty.
        """
        key = str(lxmf_hash or "").lower()
        if not key:
            return None
        now = ts if ts is not None else time.time()
        with self._lock:
            existing = self._contacts.get(key)
            contact = existing or {
                "lxmf_hash": key, "pubkey": "", "name": "",
                "id": None, "source": "inbound", "added": now, "last_seen": None,
            }
            if pubkey and not contact.get("pubkey"):
                contact["pubkey"] = str(pubkey).lower()
            if name and not contact.get("name"):
                contact["name"] = name
            contact["last_seen"] = now
            self._contacts[key] = contact
            self._save()
        return dict(contact)

    def mark_seen(self, lxmf_hash: str, ts: Optional[float] = None) -> None:
        key = str(lxmf_hash or "").lower()
        now = ts if ts is not None else time.time()
        with self._lock:
            c = self._contacts.get(key)
            if c:
                c["last_seen"] = now
                self._save()

    def remove(self, lxmf_hash: str) -> bool:
        key = str(lxmf_hash or "").lower()
        with self._lock:
            if key in self._contacts:
                del self._contacts[key]
                self._save()
                return True
        return False

    def get(self, lxmf_hash: str) -> Optional[dict]:
        key = str(lxmf_hash or "").lower()
        with self._lock:
            c = self._contacts.get(key)
            return dict(c) if c else None

    def as_list(self) -> list[dict]:
        """All contacts, most-recently-active first (added/last_seen)."""
        with self._lock:
            out = [dict(c) for c in self._contacts.values()]
        out.sort(key=lambda c: c.get("last_seen") or c.get("added") or 0, reverse=True)
        return out

    def with_keys(self) -> list[dict]:
        """Contacts that carry a public key (ready to preload into RNS)."""
        return [c for c in self.as_list() if c.get("pubkey")]

    # ── persistence (single JSON object: {lxmf_hash: contact}) ───────
    def _load(self) -> None:
        try:
            with open(self.persist_path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._contacts = {
                    str(k).lower(): v for k, v in data.items() if isinstance(v, dict)
                }
        except OSError:
            pass
        except Exception:
            self._contacts = {}

    def _save(self) -> None:
        if not self.persist_path:
            return
        try:
            d = os.path.dirname(self.persist_path)
            if d:
                os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d or None, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(self._contacts, f)
            os.replace(tmp, self.persist_path)
        except Exception:
            pass

