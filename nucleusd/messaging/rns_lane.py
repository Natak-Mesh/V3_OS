"""Reticulum/LXMF direct-message lane for the nucleus-messaging daemon.

This is the live-stack half of the Reticulum messaging feature (the pure wire
format + peer bookkeeping live in ``rns_proto.py``). It is imported and started
by ``service.py`` ONLY when ``messaging.rns.enabled`` is set, so nodes with the
lane off never pay the ~27 MB cost of loading the RNS/LXMF libraries.

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
once, 0600). It is not tied to any phone/user. Both the standard lxmf.delivery
destination and our custom nucleus.node destination are derived from it.
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

    ``on_message(peer_hash, text, ts)`` is called for each inbound LXMF message;
    the service wires it to its per-peer store. The RNS/LXMF imports happen in
    ``start`` so merely constructing the lane (e.g. in a test) pulls in nothing.
    """

    def __init__(self, appdata: dict, display_name: str,
                 announce_interval_secs: int = 1800,
                 propagation_node: bool = False,
                 on_message: Optional[Callable[[str, str, float], None]] = None):
        self.appdata = appdata
        self.display_name = display_name
        self.announce_interval = announce_interval_secs
        self.propagation_node = propagation_node
        self.on_message = on_message
        self.peers = proto.PeerTable()

        self._rns = None
        self._identity = None
        self._router = None
        self._delivery = None            # our lxmf.delivery destination
        self._node_dest = None           # our custom nucleus.node destination
        self._delivery_hash: Optional[bytes] = None
        self._stop = threading.Event()
        self._announce_thread: Optional[threading.Thread] = None
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

        # Our custom nucleus.node destination. It must be an IN destination to be
        # announceable (RNS only announces IN destinations); we never receive on
        # it — discovery is via the announce handler — so no callbacks are set.
        self._node_dest = RNS.Destination(
            self._identity, RNS.Destination.IN, RNS.Destination.SINGLE,
            proto.NUCLEUS_APP_NAME, proto.NUCLEUS_NODE_ASPECT,
        )

        if self.propagation_node:
            try:
                self._router.enable_propagation()
                log("LXMF propagation node enabled")
            except Exception as e:
                log(f"could not enable propagation node: {e}")

        self._register_announce_handler()

        self._started = True
        self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
        self._announce_thread.start()
        log(f"lane up; lxmf.delivery={RNS.prettyhexrep(self._delivery_hash)} "
            f"display={self.display_name!r}")
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

    # ── announce ────────────────────────────────────────────────
    def announce(self) -> None:
        """Announce both destinations once (lxmf.delivery + nucleus.node)."""
        if not self._started:
            return
        try:
            self._router.announce(self._delivery_hash)
        except Exception as e:
            log(f"lxmf.delivery announce error: {e}")
        try:
            self._node_dest.announce(app_data=proto.encode_node_appdata(self.appdata))
        except Exception as e:
            log(f"nucleus.node announce error: {e}")

    def _announce_loop(self) -> None:
        # First announce shortly after start so peers learn us quickly, then on
        # the configured interval.
        time.sleep(5)
        while not self._stop.is_set():
            self.announce()
            self._stop.wait(self.announce_interval)

    # ── discovery (nucleus.node announce handler) ────────────────
    def _register_announce_handler(self) -> None:
        import RNS

        lane = self

        class _NodeAnnounceHandler:
            # Only fire for our custom aspect, not every announce on the mesh.
            aspect_filter = f"{proto.NUCLEUS_APP_NAME}.{proto.NUCLEUS_NODE_ASPECT}"

            def received_announce(self, destination_hash, announced_identity,
                                  app_data):
                lane._handle_node_announce(destination_hash, announced_identity, app_data)

        RNS.Transport.register_announce_handler(_NodeAnnounceHandler())

    def _handle_node_announce(self, destination_hash, announced_identity, app_data) -> None:
        import RNS
        parsed = proto.parse_node_appdata(app_data)
        if parsed is None:
            return
        # The peer's lxmf.delivery address derives from the same identity that
        # signed this nucleus.node announce — compute it so we know where to send.
        try:
            delivery_hash = RNS.Destination.hash(
                announced_identity, proto.LXMF_APP_NAME, proto.LXMF_DELIVERY_ASPECT
            )
            delivery_hex = delivery_hash.hex()
        except Exception:
            delivery_hex = None
        hops = None
        try:
            hops = RNS.Transport.hops_to(destination_hash)
        except Exception:
            pass
        self.peers.update(parsed, delivery_hex, hops=hops)

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
        if self.on_message and text:
            self.on_message(peer_hex, text, float(ts))

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

        # We need the recipient identity to build an outbound destination. If the
        # path/identity isn't known yet, ask the network and bail for now.
        recipient_identity = RNS.Identity.recall(dest_hash)
        if recipient_identity is None:
            RNS.Transport.request_path(dest_hash)
            return {"ok": False, "error": "path unknown; requested, retry shortly"}

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
