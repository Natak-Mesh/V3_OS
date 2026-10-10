"""Persistent contact book for the Reticulum/LXMF direct-message lane.

Replaces the old announce-driven ``PeerTable`` (which was in-memory and rebuilt
from flooded ``nucleus.node`` announces). Contacts are now explicit: a node is
messageable only once its contact card has been imported (over the WiFi mesh,
pasted as a link, or scanned from a QR), or once it has messaged us (inbound
senders are auto-added — if they have our address they got it on purpose).

Each contact stores the peer's lxmf.delivery hash (the key everything is keyed
on), its 64-byte identity public key when known (so the lane can reconstruct the
recipient identity and send with no announce/path-table dependency), two names,
the ``source`` it came from (``mesh`` / ``hash`` / ``inbound``) and when it was
added. Pure and thread-safe with atomic JSON persistence — no sockets, no RNS
import — so it is fully unit-testable off-box. The live lane feeds/reads it; the
service exposes it over the control socket.

Naming. Hashes are not human-readable, so a contact carries two names:

  * ``name`` — a local nickname the operator sets (``set_name``). Never
    overwritten by the network; it always wins for display.
  * ``announced_name`` — the LXMF display name carried in the peer's
    ``lxmf.delivery`` announce, filled in when a key/name is learned (``set_key``
    / ``add_inbound``). A fallback when no nickname is set.

``display_name(contact)`` resolves the two (nickname, else announced name, else a
short hash) for the UI.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Optional

from . import rns_proto as proto


def display_name(contact: dict) -> str:
    """Human label for a contact: nickname, else announced name, else short hash.

    Pure; used by the UI/service so the resolution order lives in one place.
    """
    if not isinstance(contact, dict):
        return "?"
    nick = str(contact.get("name") or "").strip()
    if nick:
        return nick
    announced = str(contact.get("announced_name") or "").strip()
    if announced:
        return announced
    h = str(contact.get("lxmf_hash") or "")
    return h[:10] if h else "?"


class ContactStore:
    """Thread-safe contact book keyed by lxmf.delivery hash (hex), JSON-backed."""

    def __init__(self, persist_path: Optional[str] = None):
        self.persist_path = persist_path
        self._lock = threading.Lock()
        self._contacts: dict[str, dict] = {}
        if persist_path:
            self._load()

    def add_card(self, card: dict, source: str = "mesh",
                 ts: Optional[float] = None) -> dict:
        """Validate + store a contact card (e.g. pulled over the WiFi mesh).

        The card's address MUST match its key (``proto.verify_card``) or this
        raises ValueError — a card can't claim an address it has no key for. The
        card's ``name`` is the peer's advertised display name, so it is stored as
        ``announced_name`` (network-sourced); a local nickname is never touched.
        Re-importing keeps the original ``added`` time and existing nickname.
        """
        if not proto.verify_card(card):
            raise ValueError("contact card failed verification (address/key mismatch)")
        now = ts if ts is not None else time.time()
        key = str(card["lxmf_hash"]).lower()
        with self._lock:
            existing = self._contacts.get(key)
            added = existing["added"] if existing else now
            announced = str(card.get("name") or "")
            if not announced and existing:
                announced = existing.get("announced_name", "")
            contact = {
                "lxmf_hash": key,
                "pubkey": str(card["pubkey"]).lower(),
                "name": existing.get("name", "") if existing else "",
                "announced_name": announced,
                "id": card.get("id"),
                "source": existing.get("source") if existing else source,
                "added": added,
                "last_seen": existing.get("last_seen") if existing else None,
            }
            self._contacts[key] = contact
            self._save()
        return dict(contact)

    def add_hash(self, lxmf_hash: str, name: str = "",
                 ts: Optional[float] = None) -> dict:
        """Add a contact by its lxmf.delivery hash alone, with no key yet.

        This is the primary way to add any LXMF contact (Nucleus or otherwise):
        the 32-hex destination hash is all that is needed. For out-of-band entry
        when the hash was dictated, written down or read off another screen — the
        peer need not be reachable or on the mesh. An optional ``name`` is stored
        as the local nickname (hashes aren't human-readable).

        Stores a keyless contact tagged ``source="hash"``; it shows "no key yet"
        and becomes messageable once the key is learned — the operator presses
        *req path* and a path response carries the key, or the peer messages us.
        Transmits nothing. Re-adding keeps the original ``added`` time, any key
        and any existing nickname (a new non-empty ``name`` updates the nickname).

        Raises ValueError if ``lxmf_hash`` is not a 32-hex-char destination hash.
        """
        if not proto.valid_lxmf_hash(lxmf_hash):
            raise ValueError("not a valid lxmf.delivery hash (want 32 hex chars)")
        key = str(lxmf_hash).strip().lower()
        now = ts if ts is not None else time.time()
        with self._lock:
            existing = self._contacts.get(key)
            added = existing["added"] if existing else now
            nm = str(name or "").strip()
            if not nm and existing:
                nm = existing.get("name", "")
            contact = {
                "lxmf_hash": key,
                "pubkey": existing.get("pubkey", "") if existing else "",
                "name": nm,
                "announced_name": existing.get("announced_name", "") if existing else "",
                "id": existing.get("id") if existing else None,
                "source": existing.get("source", "hash") if existing else "hash",
                "added": added,
                "last_seen": existing.get("last_seen") if existing else None,
            }
            self._contacts[key] = contact
            self._save()
        return dict(contact)

    def set_name(self, lxmf_hash: str, name: str) -> Optional[dict]:
        """Set (or clear) the local nickname for an existing contact.

        Operator-driven rename. Returns the updated contact, or None if there is
        no such contact. Transmits nothing.
        """
        key = str(lxmf_hash or "").lower()
        with self._lock:
            c = self._contacts.get(key)
            if not c:
                return None
            c["name"] = str(name or "").strip()
            self._save()
            return dict(c)

    def set_key(self, lxmf_hash: str, pubkey: str,
                announced_name: str = "") -> Optional[dict]:
        """Persist a learned public key (and announced name) for a contact.

        Called once a key is recovered locally — e.g. ``Identity.recall`` returns
        it after a path response, or a signed inbound message carried it — so the
        contact survives a daemon restart without another *req path*. Writes to
        disk only; nothing is transmitted. Will not downgrade an existing key.
        Returns the updated contact, or None if there is no such contact.
        """
        key = str(lxmf_hash or "").lower()
        pub = str(pubkey or "").strip().lower()
        if not pub:
            return None
        with self._lock:
            c = self._contacts.get(key)
            if not c:
                return None
            if not c.get("pubkey"):
                c["pubkey"] = pub
            an = str(announced_name or "").strip()
            if an and not c.get("announced_name"):
                c["announced_name"] = an
            self._save()
            return dict(c)

    def add_inbound(self, lxmf_hash: str, pubkey: Optional[str],
                    name: str = "", ts: Optional[float] = None) -> Optional[dict]:
        """Auto-add/refresh a contact from an inbound message.

        Called when a message arrives from a peer we don't have yet. If we have
        the sender's public key (from the signed message or ``Identity.recall``)
        the contact is fully usable for replies; if not, it is stored keyless and
        upgraded later once the key is learned. ``last_seen`` is always bumped.
        Any ``name`` here is the peer's announced display name (network-sourced),
        so it fills ``announced_name``, never the local nickname.
        Returns the stored contact, or None if ``lxmf_hash`` is empty.
        """
        key = str(lxmf_hash or "").lower()
        if not key:
            return None
        now = ts if ts is not None else time.time()
        with self._lock:
            existing = self._contacts.get(key)
            contact = existing or {
                "lxmf_hash": key, "pubkey": "", "name": "", "announced_name": "",
                "id": None, "source": "inbound", "added": now, "last_seen": None,
            }
            contact.setdefault("announced_name", "")
            if pubkey and not contact.get("pubkey"):
                contact["pubkey"] = str(pubkey).lower()
            if name and not contact.get("announced_name"):
                contact["announced_name"] = str(name).strip()
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

