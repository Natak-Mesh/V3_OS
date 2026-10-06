"""Reticulum/LXMF direct-message lane for the nucleus-messaging daemon.

This is the live-stack half of the Reticulum messaging feature (the pure wire
format + contact helpers live in ``rns_proto.py``; the persistent contact book in
``rns_contacts.py``). It is imported and started by ``service.py`` ONLY when
``messaging.rns.enabled`` is set, so nodes with the lane off never pay the
~27 MB cost of loading the RNS/LXMF libraries.

Discovery is contact-based, not announce-based
----------------------------------------------
There is no custom flooded ``nucleus.node`` announce any more. A node is
reachable once its contact card (lxmf.delivery address + identity public key) has
been imported into the ContactStore — over the WiFi mesh, by pasting a link, or
by scanning a QR. On ``start`` every stored contact's public key is loaded into
RNS (``Identity.remember``/``recall``) so we can send to it with no path-table or
announce dependency; if a path isn't cached yet LXMF requests it and retries.
The only announce we ever emit is the standard ``lxmf.delivery`` one, and only
when asked — manually via ``announce()`` (operator button / on send) or, in
``auto`` mode, on a timer.

Hard rule — never start a second Reticulum stack
------------------------------------------------
rnsd already runs as the shared transport instance on this node. This lane is a
*client* of it: it calls ``RNS.Reticulum()`` which, when a shared instance is
up, attaches over the local control socket and opens no interfaces of its own.
To make sure we never accidentally become the instance (RNS silently promotes
the first caller to the shared instance and opens every configured interface if
rnsd is down), ``start`` first probes rnsd via ``nucleusd.reticulum`` and refuses
to initialise if it is not reachable — the caller retries later.

Identity
--------
A single NODE identity persisted at ``/var/lib/nucleus/rns/identity`` (created
once, 0600). It is not tied to any phone/user. The lxmf.delivery destination and
this node's own shareable contact card are derived from it.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .. import reticulum as reti
from . import rns_proto as proto

# Node identity + LXMF router storage live under the daemon's StateDirectory.
RNS_STATE_DIR = Path(os.environ.get("NUCLEUS_RNS_DIR", "/var/lib/nucleus/rns"))
IDENTITY_PATH = RNS_STATE_DIR / "identity"
LXMF_STORAGE = RNS_STATE_DIR / "lxmf"


def log(msg: str) -> None:
    print(f"[messaging.rns] {msg}", flush=True)


@contextlib.contextmanager
def _signals_noop_off_main_thread():
    """Neutralise signal.signal() while not on the main thread.

    Both RNS.Reticulum and LXMF.LXMRouter unconditionally register SIGINT/SIGTERM
    handlers in __init__, which raises "signal only works in main thread" when we
    build them from the daemon's background setup thread. Those handlers only
    drive a standalone instance's own shutdown — we attach as a shared-instance
    client, so skipping them is safe. On the main thread we leave signals alone.
    """
    import signal
    import threading as _t
    if _t.current_thread() is _t.main_thread():
        yield
        return
    real = signal.signal
    signal.signal = lambda *a, **k: None
    try:
        yield
    finally:
        signal.signal = real


def rnsd_available() -> bool:
    """True if the rnsd shared instance answers on its control socket.

    Guard against ever promoting ourselves to the shared instance: we only call
    RNS.Reticulum() once this returns True. Any RPC/socket error -> False.
    """
    try:
        reti._rpc({"get": "interface_stats"}, timeout=2.0)
        return True
    except reti.ReticulumError:
        return False
    except Exception:
        return False


class RnsLane:
    """Owns the RNS instance handle, the node identity and the LXMF router.

    ``on_message(peer_hash, text, ts, pubkey)`` is called for each inbound LXMF
    message (``pubkey`` is the sender's hex public key when known, else None, so
    the service can auto-add the sender as a contact). ``contacts`` is the
    persistent ``ContactStore`` whose keyed entries are loaded into RNS on start.
    The RNS/LXMF imports happen in ``start`` so merely constructing the lane
    (e.g. in a test) pulls in nothing.
    """

    def __init__(self, display_name: str,
                 node_id: Optional[int] = None,
                 announce_mode: str = "manual",
                 announce_interval_secs: int = 1800,
                 propagation_node: bool = False,
                 contacts=None,
                 on_message: Optional[Callable[..., None]] = None):
        self.display_name = display_name
        self.node_id = node_id
        self.announce_mode = announce_mode
        self.announce_interval = announce_interval_secs
        self.propagation_node = propagation_node
        self.contacts = contacts
        self.on_message = on_message

        self._rns = None
        self._identity = None
        self._router = None
        self._delivery = None            # our lxmf.delivery destination
        self._delivery_hash: Optional[bytes] = None
        self._pubkey: Optional[bytes] = None   # our identity public key (for the card)
        self._stop = threading.Event()
        self._announce_thread: Optional[threading.Thread] = None
        self._announced_for_send = False
        self._started = False

    # ── identity ────────────────────────────────────────────────
    def _load_or_create_identity(self):
        import RNS
        RNS_STATE_DIR.mkdir(parents=True, exist_ok=True)
        if IDENTITY_PATH.exists():
            ident = RNS.Identity.from_file(str(IDENTITY_PATH))
            if ident is not None:
                return ident
            log(f"identity at {IDENTITY_PATH} unreadable; regenerating")
        ident = RNS.Identity()
        ident.to_file(str(IDENTITY_PATH))
        try:
            os.chmod(IDENTITY_PATH, 0o600)
        except OSError:
            pass
        log(f"created new node identity at {IDENTITY_PATH}")
        return ident

    # ── lifecycle ───────────────────────────────────────────────
    def start(self) -> bool:
        """Attach to rnsd and bring up LXMF. Returns False if rnsd is down.

        Safe to call repeatedly: no-op once started. On a False return the
        service should retry later (rnsd may still be starting).
        """
        if self._started:
            return True
        if not rnsd_available():
            log("rnsd shared instance not reachable; deferring (will retry)")
            return False

        import RNS
        import LXMF

        # require_shared_instance=True: hard-fail rather than silently become the
        # instance ourselves if rnsd vanished between the probe and now. The
        # signal-neutralising context lets this run from the background setup
        # thread (see _signals_noop_off_main_thread).
        self._identity = self._load_or_create_identity()
        LXMF_STORAGE.mkdir(parents=True, exist_ok=True)
        with _signals_noop_off_main_thread():
            self._rns = RNS.Reticulum(require_shared_instance=True)
            self._router = LXMF.LXMRouter(
                identity=self._identity,
                storagepath=str(LXMF_STORAGE),
                autopeer=True,
            )
        self._router.register_delivery_callback(self._on_lxmf)
        self._delivery = self._router.register_delivery_identity(
            self._identity, display_name=self.display_name
        )
        self._delivery_hash = self._delivery.hash
        self._pubkey = self._identity.get_public_key()

        if self.propagation_node:
            try:
                self._router.enable_propagation()
                log("LXMF propagation node enabled")
            except Exception as e:
                log(f"could not enable propagation node: {e}")

        # Load every stored contact's public key into RNS so we can address them
        # with no announce/path dependency (replaces the old announce handler).
        self._preload_contacts()

        self._started = True
        # Only run the announce timer in auto mode. In manual mode (the default)
        # nothing is ever broadcast until announce() is called explicitly.
        if self.announce_mode == "auto":
            self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
            self._announce_thread.start()
        log(f"lane up; lxmf.delivery={RNS.prettyhexrep(self._delivery_hash)} "
            f"display={self.display_name!r} announce_mode={self.announce_mode}")
        return True

    def stop(self) -> None:
        self._stop.set()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def delivery_hash_hex(self) -> Optional[str]:
        if self._delivery_hash is None:
            return None
        return self._delivery_hash.hex()

    @property
    def pubkey_hex(self) -> Optional[str]:
        if self._pubkey is None:
            return None
        return self._pubkey.hex()

    def card(self) -> Optional[dict]:
        """This node's shareable contact card (None until the lane is up).

        The card is everything a peer needs to message us: our lxmf.delivery
        address and identity public key (plus a display name/id). Peers import it
        over the mesh, as a pasted link, or via QR — no announce involved.
        """
        if not (self._delivery_hash and self._pubkey):
            return None
        return {
            "v": proto.CARD_VERSION,
            "name": self.display_name,
            "id": self.node_id,
            "lxmf_hash": self._delivery_hash.hex(),
            "pubkey": self._pubkey.hex(),
        }

    # ── contact preload (replaces the announce handler) ──────────
    def _preload_contacts(self) -> None:
        """Teach RNS every known contact's identity so sends resolve offline.

        As a shared-instance client RNS does not persist learned identities for
        us, so each start we re-register every stored contact's public key with
        ``Identity.remember`` (keyed by its lxmf.delivery hash). After this,
        ``Identity.recall`` returns a usable identity for those peers with no
        announce ever heard — a path request fills in routing on first send.
        """
        if self.contacts is None:
            return
        n = 0
        for c in self.contacts.with_keys():
            if self.remember_contact(c.get("lxmf_hash", ""), c.get("pubkey", "")):
                n += 1
        if n:
            log(f"preloaded {n} contact identit{'y' if n == 1 else 'ies'} into RNS")

    def remember_contact(self, lxmf_hash_hex: str, pubkey_hex: str) -> bool:
        """Register one contact's public key against its lxmf.delivery hash.

        Returns True on success. Safe to call after start for a freshly imported
        contact so it is immediately messageable without a restart.
        """
        import RNS
        try:
            dest_hash = bytes.fromhex(lxmf_hash_hex)
            pub = bytes.fromhex(pubkey_hex)
        except (ValueError, TypeError):
            return False
        try:
            # packet_hash is only used for dedup/age bookkeeping; a hash of the
            # key is a stable, collision-free stand-in since we have no packet.
            RNS.Identity.remember(proto._sha256(pub), dest_hash, pub, app_data=None)
            return True
        except Exception as e:
            log(f"could not remember contact {lxmf_hash_hex[:8]}: {e}")
            return False

    # ── announce (lxmf.delivery only, on demand) ─────────────────
    def announce(self) -> bool:
        """Announce our lxmf.delivery destination once. Returns True if emitted.

        Called by the operator "Announce" button, automatically on the first
        send so a fresh contact can resolve a path back to us, and (auto mode
        only) on the interval timer. There is no custom node announce any more.
        """
        if not self._started:
            return False
        try:
            self._router.announce(self._delivery_hash)
            return True
        except Exception as e:
            log(f"lxmf.delivery announce error: {e}")
            return False

    def _announce_loop(self) -> None:
        # Auto mode only. First announce shortly after start, then on the interval.
        time.sleep(5)
        while not self._stop.is_set():
            self.announce()
            self._stop.wait(self.announce_interval)

    # ── inbound LXMF ─────────────────────────────────────────────
    def _on_lxmf(self, message) -> None:
        try:
            text = message.content.decode("utf-8", "ignore") if message.content else ""
            src = message.source_hash
            peer_hex = src.hex() if src else "?"
            ts = getattr(message, "timestamp", None) or time.time()
        except Exception as e:
            log(f"inbound LXMF parse error: {e}")
            return
        # Capture the sender's public key when the message carried a validated
        # source identity (or RNS already knows it), so the service can auto-add
        # the sender as a fully-usable contact for replies.
        pubkey_hex = self._sender_pubkey(src)
        if self.on_message and text:
            self.on_message(peer_hex, text, float(ts), pubkey_hex)

    def _sender_pubkey(self, src_hash) -> Optional[str]:
        """Best-effort hex public key for an inbound sender, or None."""
        if not src_hash:
            return None
        import RNS
        try:
            ident = RNS.Identity.recall(src_hash)
            if ident is not None:
                return ident.get_public_key().hex()
        except Exception:
            pass
        return None

    # ── outbound LXMF ────────────────────────────────────────────
    def send(self, dest_hash_hex: str, text: str) -> dict:
        """Send a direct LXMF message to a node's lxmf.delivery hash (hex).

        Returns ``{"ok": True, "state": "..."}`` once handed to the router, or
        ``{"ok": False, "error": ...}``. Path resolution/delivery happen async in
        the router; delivery/failure is reflected later via LXMF state.
        """
        if not self._started:
            return {"ok": False, "error": "rns lane not started"}
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty message"}
        import RNS
        import LXMF
        try:
            dest_hash = bytes.fromhex(dest_hash_hex)
        except ValueError:
            return {"ok": False, "error": "bad destination hash"}

        # The recipient identity is needed to build an outbound destination. For
        # an imported contact it was loaded by _preload_contacts/remember_contact,
        # so recall() returns it even though we've never heard an announce. If it
        # is genuinely unknown (no contact), request a path and bail — but this is
        # the exception now, not the norm.
        recipient_identity = RNS.Identity.recall(dest_hash)
        if recipient_identity is None:
            RNS.Transport.request_path(dest_hash)
            return {"ok": False, "error": "unknown contact; no key on file — import its card"}

        # Announce our own delivery destination once so the recipient can resolve
        # a path back to us (we never announce on a timer in manual mode). First
        # outbound to a peer also triggers a path request on their side via LXMF.
        if not self._announced_for_send:
            self.announce()
            RNS.Transport.request_path(dest_hash)
            self._announced_for_send = True

        dest = RNS.Destination(
            recipient_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
            proto.LXMF_APP_NAME, proto.LXMF_DELIVERY_ASPECT,
        )
        lxm = LXMF.LXMessage(
            dest, self._delivery, text,
            desired_method=LXMF.LXMessage.DIRECT,
        )
        try:
            self._router.handle_outbound(lxm)
        except Exception as e:
            return {"ok": False, "error": f"send failed: {e}"}
        return {"ok": True, "state": "outbound"}
