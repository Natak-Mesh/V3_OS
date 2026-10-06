#!/usr/bin/env python3
"""nucleus-messaging — text messaging over WiFi + LoRa into one store.

Single daemon per node. Owns ONE MessageStore; two transports deliver into it
and one send path fans out to both:

    WiFi UDP mcast ─┐
                    ├─► MessageStore (dedupe) ─► control socket ─► FastAPI/UI
    LoRa (portnum 1)┘         ▲
     via cot_bridge relay     │
                              └─ send(text): fan out to BOTH transports

- WiFi transport: UDP multicast on the wlan1 802.11s mesh. The wire frame is a
  tiny JSON line {sender, text, ts}; only Nucleus nodes speak it.
- LoRa transport: standard Meshtastic TEXT_MESSAGE_APP via the CoT bridge's
  localhost UDP relay (bridge owns the radio). Plain Meshtastic radios on the
  same channel interoperate — their messages arrive here via the same relay.

Control socket (UDP 127.0.0.1:5562, one JSON request per datagram, JSON reply):
    {"cmd":"send","text":"..."}          -> {"ok":true,"id":"..."}
    {"cmd":"history","since":<epoch>}    -> {"ok":true,"messages":[...]}
    {"cmd":"status"}                     -> {"ok":true,...}

Config comes from /etc/nucleus/config.yaml (messaging.* + node identity).
Run via nucleus-messaging.service. See docs/manual/messaging-internals.md.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

from .store import MessageStore

CONFIG_PATH = os.environ.get("NUCLEUS_CONFIG", "/etc/nucleus/config.yaml")

# CoT-bridge LoRa text relay endpoints (must match cot_bridge.py).
LORA_TX_ADDR = ("127.0.0.1", 5560)   # us -> bridge -> LoRa TX
LORA_RX_ADDR = ("127.0.0.1", 5561)   # bridge -> us (we bind here)

CONTROL_ADDR = ("127.0.0.1", 5562)   # API/CLI -> us (we bind here)

MCAST_IF = "wlan1"                    # WiFi transport egresses the mesh iface
PERSIST_PATH = "/var/lib/nucleus/messages.jsonl"
RNS_PERSIST_PATH = "/var/lib/nucleus/rns_messages.jsonl"
RNS_CONTACTS_PATH = "/var/lib/nucleus/rns/contacts.json"
RNS_RETRY_SECS = 30                   # retry attaching to rnsd while it's down


def log(msg: str) -> None:
    print(f"[messaging] {msg}", flush=True)


def iface_ipv4(ifname: str) -> str | None:
    """Return the primary IPv4 address of ``ifname``, or None if unavailable.

    Used to scope multicast TX/join to the mesh interface without needing
    CAP_NET_RAW (SO_BINDTODEVICE requires it; IP_MULTICAST_IF / the join's
    interface address do not, so this works for the unprivileged daemon).
    """
    import fcntl
    import struct
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = struct.pack("256s", ifname.encode("utf-8")[:15])
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24])
    except OSError:
        return None
    finally:
        s.close()


def load_config() -> dict:
    """Load messaging.* + derived identity via the validated schema.

    Goes through ``nucleusd.config.load`` (not a bespoke YAML read) so this
    daemon shares the single config contract and derived values — per the repo
    rule that new code loads config through ``config.load()``. The returned flat
    dict keeps the shape the rest of the service already consumes; the validated
    model is stashed under ``_cfg`` so the RNS lane can derive its announce
    app_data (node id/host/IPs/version/caps) from the same source of truth.

    Falls back to safe defaults only if the config is unreadable/invalid, so the
    daemon still starts (WiFi lane) on a half-provisioned node.
    """
    defaults = {
        "enabled": True,
        "wifi_group": "239.10.10.60",
        "wifi_port": 17020,
        "lora": True,
        "dedupe_window_secs": 60,
        "history_limit": 500,
        "node_id": 0,
        "sender": socket.gethostname().split(".")[0],
        "mesh_ttl": 8,
        "rns_enabled": False,
        "rns_announce_mode": "manual",
        "rns_announce_interval_secs": 1800,
        "rns_propagation_node": False,
        "_cfg": None,
    }
    try:
        from .. import config as cfgmod
        cfg = cfgmod.load()
    except Exception as e:
        log(f"config load error ({e}); using defaults")
        return defaults

    m = cfg.messaging
    out = dict(defaults)
    out.update({
        "enabled": m.enabled,
        "wifi_group": m.wifi_group,
        "wifi_port": m.wifi_port,
        "lora": m.lora,
        "dedupe_window_secs": m.dedupe_window_secs,
        "history_limit": m.history_limit,
        "node_id": cfg.node.id,
        "sender": cfg.node.hostname,
        "mesh_ttl": cfg.mesh.mesh_802_ttl,
        "rns_enabled": m.rns.enabled,
        "rns_announce_mode": m.rns.announce_mode,
        "rns_announce_interval_secs": m.rns.announce_interval_secs,
        "rns_propagation_node": m.rns.propagation_node,
        "_cfg": cfg,
    })
    return out


class MessagingService:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sender = cfg["sender"]
        self.store = MessageStore(
            dedupe_window_secs=cfg["dedupe_window_secs"],
            history_limit=cfg["history_limit"],
            persist_path=PERSIST_PATH,
        )
        self.wifi_group = cfg["wifi_group"]
        self.wifi_port = int(cfg["wifi_port"])
        self.lora_enabled = bool(cfg["lora"])
        self._wifi_tx = None
        self._lora_tx = None
        self._notify_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Local push subscribers (API WS bridges): addr -> expiry epoch.
        self._subs: dict = {}
        self._subs_lock = threading.Lock()
        self._stop = threading.Event()

        # ── Reticulum/LXMF direct-message lane (optional) ────────────
        # Off unless messaging.rns.enabled. Built lazily in run() so the
        # RNS/LXMF libraries are never imported on nodes with the lane off.
        self.rns_enabled = bool(cfg.get("rns_enabled", False))
        self._rns_lane = None
        self._rns_store = None
        self._rns_contacts = None
        if self.rns_enabled:
            from .rns_store import ConversationStore
            from .rns_contacts import ContactStore
            self._rns_store = ConversationStore(
                history_limit=cfg["history_limit"],
                persist_path=RNS_PERSIST_PATH,
            )
            # Persistent contact book (replaces the old announce-driven peer
            # table). Loaded here so imports work even before the lane attaches.
            self._rns_contacts = ContactStore(persist_path=RNS_CONTACTS_PATH)

    # ── local push subscribers (for live UI over WS) ────────────
    SUB_TTL = 20.0  # a subscriber must re-subscribe within this window

    def _subscribe(self, addr) -> None:
        with self._subs_lock:
            self._subs[addr] = time.time() + self.SUB_TTL

    def _notify(self, msg: dict) -> None:
        """Push a new message to all live subscribers (best-effort)."""
        now = time.time()
        frame = json.dumps({"event": "message", "message": msg}).encode("utf-8")
        with self._subs_lock:
            dead = [a for a, exp in self._subs.items() if exp < now]
            for a in dead:
                del self._subs[a]
            targets = list(self._subs.keys())
        for a in targets:
            try:
                self._notify_tx.sendto(frame, a)
            except OSError:
                pass

    def _ingest(self, sender, text, transport, ts=None, mine=False):
        """store.ingest + push to subscribers when the message is new."""
        msg, is_new = self.store.ingest(sender, text, transport, ts=ts, mine=mine)
        if is_new:
            self._notify(msg)
        return msg, is_new

    # ── send: fan out to BOTH transports ────────────────────────
    def send(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty message"}
        # Local echo into the store first so the UI shows it immediately.
        msg, _ = self._ingest(self.sender, text, "wifi", mine=True)
        self._send_wifi(text)
        if self.lora_enabled:
            self._send_lora(text)
        return {"ok": True, "id": msg["id"]}

    def _send_wifi(self, text: str) -> None:
        try:
            frame = json.dumps(
                {"sender": self.sender, "text": text, "ts": time.time()}
            ).encode("utf-8")
            self._wifi_tx.sendto(frame, (self.wifi_group, self.wifi_port))
        except Exception as e:
            log(f"wifi TX error: {e}")

    def _send_lora(self, text: str) -> None:
        try:
            self._lora_tx.sendto(text.encode("utf-8"), LORA_TX_ADDR)
        except Exception as e:
            log(f"lora TX error: {e}")

    # ── WiFi transport ──────────────────────────────────────────
    def _wifi_rx_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.wifi_port))
        # Join on the mesh interface's address, not INADDR_ANY. With 0.0.0.0 the
        # kernel joins on the default-route iface (eth0 here), so multicast from
        # the mesh never reaches us. Scope the join to wlan1's IP.
        if_ip = iface_ipv4(MCAST_IF)
        join_ip = if_ip or "0.0.0.0"
        if if_ip is None:
            log(f"WARNING: {MCAST_IF} has no IPv4 addr; "
                f"joining multicast on default iface (RX may not work)")
        mreq = socket.inet_aton(self.wifi_group) + socket.inet_aton(join_ip)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        log(f"WiFi RX on {self.wifi_group}:{self.wifi_port}")
        while not self._stop.is_set():
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                obj = json.loads(data.decode("utf-8"))
                sender = str(obj.get("sender", "?"))
                text = str(obj.get("text", ""))
                ts = float(obj.get("ts", time.time()))
            except Exception:
                continue
            if not text:
                continue
            # Skip our own multicast echo (we already stored it on send).
            if sender == self.sender:
                continue
            self._ingest(sender, text, "wifi", ts=ts)
        sock.close()

    def _setup_wifi_tx(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        ttl = int(self.cfg.get("mesh_ttl", 8))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        # Egress multicast out the mesh interface. SO_BINDTODEVICE needs
        # CAP_NET_RAW (we run unprivileged as User=natak, so it silently fails
        # and TX follows the default route out eth0). IP_MULTICAST_IF, set to
        # wlan1's address, pins the egress iface without any capability.
        if_ip = iface_ipv4(MCAST_IF)
        if if_ip is not None:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                         socket.inet_aton(if_ip))
        else:
            log(f"WARNING: {MCAST_IF} has no IPv4 addr; "
                f"multicast TX will follow the default route (may not reach mesh)")
        self._wifi_tx = s

    # ── LoRa transport (via cot_bridge relay) ───────────────────
    def _lora_rx_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(LORA_RX_ADDR)
        except OSError as e:
            log(f"LoRa RX bind failed ({e}) — LoRa receive disabled")
            return
        sock.settimeout(1.0)
        log(f"LoRa RX on {LORA_RX_ADDR[0]}:{LORA_RX_ADDR[1]}")
        while not self._stop.is_set():
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            # Frame from bridge: len(name)(B) + name + utf-8 text payload.
            if len(data) < 1:
                continue
            nlen = data[0]
            sender = data[1:1 + nlen].decode("utf-8", "ignore") or "?"
            text = data[1 + nlen:].decode("utf-8", "ignore")
            if not text:
                continue
            self._ingest(sender, text, "lora")
        sock.close()

    # ── Control socket (API/CLI) ────────────────────────────────
    def _control_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(CONTROL_ADDR)
        sock.settimeout(1.0)
        log(f"Control on {CONTROL_ADDR[0]}:{CONTROL_ADDR[1]}")
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                req = json.loads(data.decode("utf-8"))
            except Exception:
                self._reply(sock, addr, {"ok": False, "error": "bad json"})
                continue
            # subscribe: remember the caller's addr for live push notifications.
            if req.get("cmd") == "subscribe":
                self._subscribe(addr)
                self._reply(sock, addr, {"ok": True, "ttl": self.SUB_TTL})
                continue
            self._reply(sock, addr, self._handle(req))
        sock.close()

    def _handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "send":
            return self.send(req.get("text", ""))
        if cmd == "history":
            since = float(req.get("since", 0) or 0)
            return {"ok": True, "messages": self.store.history(since)}
        if cmd == "status":
            return {
                "ok": True,
                "sender": self.sender,
                "wifi_group": self.wifi_group,
                "wifi_port": self.wifi_port,
                "lora": self.lora_enabled,
                "count": len(self.store.history()),
                "rns": self._rns_status(),
            }
        # ── Reticulum/LXMF direct-message commands ───────────────
        if cmd == "rns_contacts":
            return self._rns_contacts_list()
        if cmd == "rns_history":
            return self._rns_history(req.get("peer"), float(req.get("since", 0) or 0))
        if cmd == "rns_send":
            return self._rns_send(req.get("dest", ""), req.get("text", ""))
        if cmd == "rns_card":
            return self._rns_card()
        if cmd == "rns_import":
            return self._rns_import(req.get("link"), req.get("card"),
                                    req.get("source", "link"))
        if cmd == "rns_remove":
            return self._rns_remove(req.get("dest", ""))
        if cmd == "rns_announce":
            return self._rns_announce()
        return {"ok": False, "error": f"unknown cmd {cmd!r}"}

    # ── Reticulum/LXMF lane helpers ──────────────────────────────
    def _rns_status(self) -> dict:
        if not self.rns_enabled:
            return {"enabled": False}
        lane = self._rns_lane
        return {
            "enabled": True,
            "started": bool(lane and lane.started),
            "address": lane.delivery_hash_hex if lane else None,
            "announce_mode": self.cfg.get("rns_announce_mode", "manual"),
        }

    def _rns_contacts_list(self) -> dict:
        """Imported contacts (replaces the old announce-discovered peer list)."""
        if not (self.rns_enabled and self._rns_contacts):
            return {"ok": True, "contacts": []}
        return {"ok": True, "contacts": self._rns_contacts.as_list()}

    def _rns_card(self) -> dict:
        """This node's shareable contact card + its nucleus-rns:// link."""
        if not (self.rns_enabled and self._rns_lane and self._rns_lane.started):
            return {"ok": False, "error": "rns lane not available"}
        card = self._rns_lane.card()
        if not card:
            return {"ok": False, "error": "card not ready"}
        from . import rns_proto as proto
        return {"ok": True, "card": card, "link": proto.encode_card(card)}

    def _rns_import(self, link, card, source: str) -> dict:
        """Import a contact from a nucleus-rns:// link or a raw card dict.

        Verifies the card (address must match key) and, if the lane is up, loads
        the key into RNS straight away so the contact is messageable with no
        restart. Returns the stored contact.
        """
        if not (self.rns_enabled and self._rns_contacts):
            return {"ok": False, "error": "rns lane not available"}
        from . import rns_proto as proto
        parsed = card if isinstance(card, dict) else proto.decode_card(link)
        if parsed is None:
            return {"ok": False, "error": "could not parse contact card"}
        if not proto.verify_card(parsed):
            return {"ok": False, "error": "card failed verification (address/key mismatch)"}
        try:
            contact = self._rns_contacts.add_card(parsed, source=source)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if self._rns_lane and self._rns_lane.started:
            self._rns_lane.remember_contact(contact["lxmf_hash"], contact["pubkey"])
        return {"ok": True, "contact": contact}

    def _rns_remove(self, dest: str) -> dict:
        if not (self.rns_enabled and self._rns_contacts):
            return {"ok": False, "error": "rns lane not available"}
        return {"ok": True, "removed": self._rns_contacts.remove(dest)}

    def _rns_announce(self) -> dict:
        """Emit our lxmf.delivery announce once (operator-triggered)."""
        if not (self.rns_enabled and self._rns_lane and self._rns_lane.started):
            return {"ok": False, "error": "rns lane not available"}
        return {"ok": True, "announced": self._rns_lane.announce()}

    def _rns_history(self, peer, since: float) -> dict:
        if not (self.rns_enabled and self._rns_store):
            return {"ok": True, "messages": []}
        return {"ok": True, "messages": self._rns_store.history(peer, since)}

    def _rns_send(self, dest: str, text: str) -> dict:
        if not (self.rns_enabled and self._rns_lane and self._rns_lane.started):
            return {"ok": False, "error": "rns lane not available"}
        res = self._rns_lane.send(dest, text)
        if res.get("ok"):
            msg = self._rns_store.add(dest, text.strip(), "out",
                                      state=res.get("state", "outbound"))
            self._notify_rns(msg)
            res["id"] = f"{msg['peer']}:{msg['ts']}"
        return res

    def _on_rns_message(self, peer_hex: str, text: str, ts: float,
                        pubkey_hex: str | None = None) -> None:
        """Inbound LXMF callback: auto-add the sender, store, push to subscribers.

        Unknown senders are added as contacts automatically — if a node has our
        address it got the card on purpose, so a reply path should just work. If
        the sender's public key was recovered it is stored (reply-ready); if not,
        the contact is keyless until the key is learned.
        """
        if not self._rns_store:
            return
        if self._rns_contacts:
            self._rns_contacts.add_inbound(peer_hex, pubkey_hex, ts=ts)
        msg = self._rns_store.add(peer_hex, text, "in", ts=ts)
        self._notify_rns(msg)

    def _notify_rns(self, msg: dict) -> None:
        """Push a new DM to live subscribers (same channel as broadcast)."""
        now = time.time()
        frame = json.dumps({"event": "rns_message", "message": msg}).encode("utf-8")
        with self._subs_lock:
            targets = [a for a, exp in self._subs.items() if exp >= now]
        for a in targets:
            try:
                self._notify_tx.sendto(frame, a)
            except OSError:
                pass

    def _rns_setup_loop(self) -> None:
        """Build + attach the RNS lane, retrying until rnsd is reachable."""
        from .rns_lane import RnsLane
        self._rns_lane = RnsLane(
            display_name=self.sender,
            node_id=self.cfg.get("node_id"),
            announce_mode=self.cfg.get("rns_announce_mode", "manual"),
            announce_interval_secs=self.cfg.get("rns_announce_interval_secs", 1800),
            propagation_node=self.cfg.get("rns_propagation_node", False),
            contacts=self._rns_contacts,
            on_message=self._on_rns_message,
        )
        while not self._stop.is_set():
            if self._rns_lane.start():
                log("rns lane attached to rnsd shared instance")
                return
            self._stop.wait(RNS_RETRY_SECS)

    @staticmethod
    def _reply(sock, addr, obj) -> None:
        try:
            sock.sendto(json.dumps(obj).encode("utf-8"), addr)
        except Exception:
            pass

    # ── lifecycle ───────────────────────────────────────────────
    def run(self) -> None:
        self._setup_wifi_tx()
        self._lora_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        threads = [
            threading.Thread(target=self._wifi_rx_loop, daemon=True),
            threading.Thread(target=self._control_loop, daemon=True),
        ]
        if self.lora_enabled:
            threads.append(threading.Thread(target=self._lora_rx_loop, daemon=True))
        if self.rns_enabled:
            # Attaches to rnsd in the background (retries if rnsd isn't up yet),
            # so a down/slow-to-start rnsd never blocks the WiFi/LoRa lanes.
            threads.append(threading.Thread(target=self._rns_setup_loop, daemon=True))
        for t in threads:
            t.start()
        log(f"messaging up as sender={self.sender!r} "
            f"(wifi={self.wifi_group}:{self.wifi_port}, lora={self.lora_enabled}, "
            f"rns={self.rns_enabled})")
        try:
            while not self._stop.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        self._stop.set()


def main() -> None:
    cfg = load_config()
    if not cfg.get("enabled", True):
        log("messaging disabled in config; exiting")
        sys.exit(0)
    MessagingService(cfg).run()


if __name__ == "__main__":
    main()
