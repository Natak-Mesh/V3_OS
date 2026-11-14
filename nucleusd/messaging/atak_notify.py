"""ATAK node notifications — node events as direct GeoChat to the local ATAK EUD.

Runs inside nucleus-messaging (no LoRa / cot-bridge dependency). Parts:

  * DeviceTable — discovers the ATAK device(s) on THIS node's br-lan from their
    SA multicast (239.2.3.1:6969). Only senders inside the node's br-lan subnet
    are accepted, never the node itself (cot-bridge re-injects LoRa CoT from the
    br-lan IP). The unicast target comes from the SA's
    <contact endpoint="ip:port:proto">, as ATAK's own contact discovery does
    (commoncommo contact-rxtx-rules): tcp -> ip:port, srctcp -> source ip:port,
    udp -> ip:17012, stcp (TAK server) -> ignored.
  * LinkTracker — per-link (iface, neighbour link-local) Babel state with a
    startup baseline and a hold-time debounce. A failed/incomplete babeld read
    is skipped, never taken as "all links lost".
  * AtakNotifier — builds a direct GeoChat (b-t-f) per device and delivers it by
    unicast only (TCP or UDP per the device's endpoint). No multicast fallback:
    with no fresh local device, or on a send error, the notification is dropped
    and counted/logged. Nothing is held for a disconnected device beyond a short
    in-memory queue (QUEUE_EXPIRY_SECS).

Wire formats are pinned by tests to a real ATAK-CIV 5.4 SA packet and to the
GeoChat layout verified on a real device.
"""

from __future__ import annotations

import ipaddress
import queue
import socket
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

SA_GROUP = "239.2.3.1"
SA_PORT = 6969
CHAT_UDP_PORT = 17012              # ATAK unicast UDP chat port (contact rules)
TAK_MESH_HEADER = b"\xbf\x01\xbf"  # TAK Protocol V1 mesh header
SA_MAX_BYTES = 8192
SEND_TIMEOUT = 3.0
QUEUE_MAX = 32
QUEUE_EXPIRY_SECS = 60             # older notifications are dropped, not sent late
LINK_POLL_SECS = 5
LINK_FORGET_SECS = 86400           # forget a lost link after a day
BABEL_INF = 65535                  # babeld cost of an unusable link
IP_MULTICAST_ALL = 49              # linux/in.h; not exported by the socket module

IFACE_LABEL = {"wlan1": "Wi-Fi mesh", "halow0": "HaLow mesh", "eth0": "eth0 link"}


def log(msg: str) -> None:
    print(f"[atak-notify] {msg}", flush=True)


# ── TAK Protocol V1 (protobuf) — minimal reader ─────────────────────
# Only uid/type/contact are needed; a tiny wire-format walker avoids loading
# protobuf + takproto (~8 MB RSS) into this daemon on 1 GB nodes.

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("bad varint")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def _pb_fields(buf: bytes):
    """Yield (field_number, value) for a protobuf message; len-delimited -> bytes."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        fno, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _varint(buf, i)
        elif wt in (1, 5):
            n = 8 if wt == 1 else 4
            val, i = buf[i:i + n], i + n
        elif wt == 2:
            n, i = _varint(buf, i)
            val, i = buf[i:i + n], i + n
        else:
            raise ValueError(f"wire type {wt}")
        if i > len(buf):
            raise ValueError("truncated field")
        yield fno, val


def _pb_get(buf: bytes, fno: int) -> bytes | None:
    for f, v in _pb_fields(buf):
        if f == fno and isinstance(v, bytes):
            return v
    return None


def _parse_v1(payload: bytes) -> dict | None:
    # TakMessage.cotEvent(2) -> CotEvent{type(1), uid(5), detail(15)}
    # Detail.contact(2) -> Contact{endpoint(1), callsign(2)}
    ev = _pb_get(payload, 2)
    if ev is None:
        return None
    out = {"uid": "", "type": "", "endpoint": "", "callsign": ""}
    detail = None
    for f, v in _pb_fields(ev):
        if not isinstance(v, bytes):
            continue
        if f == 1:
            out["type"] = v.decode("utf-8", "replace")
        elif f == 5:
            out["uid"] = v.decode("utf-8", "replace")
        elif f == 15:
            detail = v
    contact = _pb_get(detail, 2) if detail is not None else None
    if contact is not None:
        for f, v in _pb_fields(contact):
            if f == 1 and isinstance(v, bytes):
                out["endpoint"] = v.decode("utf-8", "replace")
            elif f == 2 and isinstance(v, bytes):
                out["callsign"] = v.decode("utf-8", "replace")
    return out


def _parse_xml(data: bytes) -> dict | None:
    root = ET.fromstring(data)
    if root.tag != "event":
        return None
    out = {"uid": root.get("uid", ""), "type": root.get("type", ""),
           "endpoint": "", "callsign": ""}
    contact = root.find("detail/contact")
    if contact is not None:
        out["endpoint"] = contact.get("endpoint", "")
        out["callsign"] = contact.get("callsign", "")
    return out


def parse_sa(data: bytes) -> dict | None:
    """uid/type/endpoint/callsign from an ATAK SA datagram (V1 mesh or XML), else None."""
    if not data or len(data) > SA_MAX_BYTES:
        return None
    try:
        if data[:3] == TAK_MESH_HEADER:
            return _parse_v1(data[3:])
        if data.lstrip()[:1] == b"<":
            return _parse_xml(data)
    except (ValueError, ET.ParseError):
        return None
    return None


def resolve_endpoint(endpoint: str, src_ip: str) -> tuple[str, int, str] | None:
    """Unicast (ip, port, "tcp"|"udp") for an ATAK contact endpoint, or None."""
    parts = (endpoint or "").split(":")
    if len(parts) != 3:
        return None
    ip, port_s, proto = parts
    proto = proto.lower()
    if proto == "srctcp":
        ip, proto = src_ip, "tcp"
    if proto == "udp":
        return ip, CHAT_UDP_PORT, "udp"
    if proto != "tcp":
        return None              # stcp (TAK server) or unknown: not a direct target
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not 0 < port < 65536:
        return None
    return ip, port, "tcp"



# ── Device discovery ───────────────────────────────────────────────

class DeviceTable:
    """Local ATAK devices keyed by uid, from SA traffic on this node's br-lan."""

    def __init__(self, subnet: str, own_ip: str, stale_secs: int):
        self.subnet = ipaddress.ip_network(subnet, strict=False)
        self.own_ip = own_ip
        self.stale_secs = stale_secs
        self._lock = threading.Lock()
        self._devices: dict[str, dict] = {}

    def _local(self, ip: str) -> bool:
        try:
            return ip != self.own_ip and ipaddress.ip_address(ip) in self.subnet
        except ValueError:
            return False

    def observe(self, data: bytes, src_ip: str, now: float) -> dict | None:
        """Fold one SA datagram in; returns the device entry if accepted."""
        if not self._local(src_ip):
            return None
        sa = parse_sa(data)
        if not sa or not sa["uid"] or not sa["type"].startswith("a-"):
            return None
        target = resolve_endpoint(sa["endpoint"], src_ip)
        if target is None or not self._local(target[0]):
            return None
        dev = {"uid": sa["uid"], "callsign": sa["callsign"] or sa["uid"],
               "ip": target[0], "port": target[1], "proto": target[2],
               "last_seen": now}
        with self._lock:
            prev = self._devices.get(sa["uid"])
            self._devices[sa["uid"]] = dev
        if prev is None or (prev["ip"], prev["port"], prev["proto"]) != target:
            log(f"ATAK device {dev['callsign']} ({dev['uid']}) at "
                f"{dev['ip']}:{dev['port']}/{dev['proto']}")
        return dev

    def fresh(self, now: float) -> list[dict]:
        """Devices heard within stale_secs; older entries are forgotten."""
        with self._lock:
            for uid, d in list(self._devices.items()):
                if now - d["last_seen"] > self.stale_secs:
                    del self._devices[uid]
            return [dict(d) for d in self._devices.values()]


# ── GeoChat ────────────────────────────────────────────────────────

def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def build_geochat(sender_uid: str, sender_callsign: str, recipient_uid: str,
                  text: str, now: datetime | None = None,
                  msg_id: str | None = None) -> bytes:
    """A direct GeoChat CoT event (XML) from this node to one ATAK contact.

    Layout matches ATAK's own 1:1 GeoChat (verified on ATAK-CIV 5.4): the
    conversation is keyed by the sender uid/callsign, so every notification from
    this node lands in one chat thread named after the node.
    """
    now = now or datetime.now(timezone.utc)
    msg_id = msg_id or str(uuid.uuid4())
    t, stale = _iso(now), _iso(now + timedelta(days=1))
    ev = ET.Element("event", {
        "version": "2.0", "uid": f"GeoChat.{sender_uid}.{recipient_uid}.{msg_id}",
        "type": "b-t-f", "how": "h-g-i-g-o", "time": t, "start": t, "stale": stale,
    })
    ET.SubElement(ev, "point", {"lat": "0.0", "lon": "0.0", "hae": "9999999.0",
                                "ce": "9999999.0", "le": "9999999.0"})
    detail = ET.SubElement(ev, "detail")
    chat = ET.SubElement(detail, "__chat", {
        "parent": "RootContactGroup", "groupOwner": "false", "messageId": msg_id,
        "chatroom": sender_callsign, "id": sender_uid, "senderCallsign": sender_callsign,
    })
    ET.SubElement(chat, "chatgrp", {"uid0": sender_uid, "uid1": recipient_uid,
                                    "id": sender_uid})
    ET.SubElement(detail, "link", {"uid": sender_uid, "type": "a-f-G-U-C",
                                   "relation": "p-p"})
    remarks = ET.SubElement(detail, "remarks", {
        "source": f"BAO.F.ATAK.{sender_uid}", "to": recipient_uid, "time": t})
    remarks.text = text
    return (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            + ET.tostring(ev, encoding="utf-8", xml_declaration=False))


def send_unicast(ip: str, port: int, proto: str, data: bytes) -> None:
    """Deliver one CoT to one device. Raises OSError on failure. Never multicast."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as e:
        raise OSError(str(e)) from None
    if addr.is_multicast or addr.is_unspecified or ip.endswith(".255"):
        raise OSError(f"refusing non-unicast destination {ip}")
    if proto == "tcp":
        with socket.create_connection((ip, port), timeout=SEND_TIMEOUT) as s:
            s.sendall(data)
    elif proto == "udp":
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(data, (ip, port))
    else:
        raise OSError(f"unsupported proto {proto!r}")


# ── Mesh link tracking ─────────────────────────────────────────────

def node_label(ipv4: str | None, address: str) -> str:
    """'0047-nucleus (10.20.1.47)' from a mesh IPv4, else the link-local."""
    if ipv4:
        try:
            return f"{int(ipv4.rsplit('.', 1)[1]):04d}-nucleus ({ipv4})"
        except ValueError:
            return ipv4
    return address


class LinkTracker:
    """Debounced per-link up/down from successive Babel neighbour snapshots.

    A link is (iface, neighbour link-local). It is up while babeld lists it with
    a finite cost; down when it is missing or at infinite cost. A state change
    must persist for hold_secs before it is reported. Links seen for the first
    time (incl. the startup baseline) are adopted silently; "restored" is only
    reported for a link whose loss was reported.
    """

    def __init__(self, ifaces: list[str], hold_secs: int):
        self.ifaces = set(ifaces)
        self.hold = hold_secs
        self._links: dict[tuple[str, str], dict] = {}

    @staticmethod
    def _up(n: dict) -> bool:
        try:
            return int(n.get("cost", BABEL_INF)) < BABEL_INF
        except (TypeError, ValueError):
            return False

    def update(self, nbrs: list[dict] | None, now: float) -> list[dict]:
        """Fold one snapshot; returns [{event: lost|restored, iface, node}].

        nbrs=None (failed/incomplete babeld read) is ignored entirely.
        """
        if nbrs is None:
            return []
        seen: dict[tuple[str, str], dict] = {}
        for n in nbrs:
            iface, addr = n.get("if"), n.get("address")
            if iface in self.ifaces and addr:
                seen[(iface, addr)] = n
        events: list[dict] = []
        for key, n in seen.items():
            up = self._up(n)
            link = self._links.get(key)
            if link is None:
                link = self._links[key] = {"up": up, "since": None, "ipv4": None,
                                           "lost_notified": False}
            if n.get("ipv4"):
                link["ipv4"] = n["ipv4"]
            link["last_present"] = now
            self._observe(key, link, up, now, events)
        for key, link in list(self._links.items()):
            if key in seen:
                continue
            if now - link.get("last_present", now) > LINK_FORGET_SECS:
                del self._links[key]
                continue
            self._observe(key, link, False, now, events)
        return events

    def _observe(self, key, link: dict, up: bool, now: float, events: list) -> None:
        if up == link["up"]:
            link["since"] = None
            return
        if link["since"] is None:
            link["since"] = now
        if now - link["since"] < self.hold:
            return
        link["up"], link["since"] = up, None
        ev = {"iface": key[0], "node": node_label(link["ipv4"], key[1])}
        if not up:
            link["lost_notified"] = True
            events.append({"event": "lost", **ev})
        elif link["lost_notified"]:
            link["lost_notified"] = False
            events.append({"event": "restored", **ev})


def link_event_text(ev: dict) -> str:
    label = IFACE_LABEL.get(ev["iface"], ev["iface"])
    state = "LOST" if ev["event"] == "lost" else "restored"
    return f"{label} link {state}: {ev['node']} on {ev['iface']}"



# ── Notifier ───────────────────────────────────────────────────────

class AtakNotifier:
    """Discovery, link monitoring and unicast delivery threads + triggers."""

    def __init__(self, *, node_id: int, callsign: str, br_lan_ip: str,
                 br_lan_subnet: str, link_loss: bool, link_ifaces: list[str],
                 link_hold_secs: int, rns_message: bool, device_stale_secs: int,
                 neighbours_fn=None, send_fn=send_unicast):
        self.sender_uid = f"NUCLEUS-{node_id:04d}"
        self.callsign = callsign
        self.br_lan_ip = br_lan_ip
        self.devices = DeviceTable(br_lan_subnet, br_lan_ip, device_stale_secs)
        self.link_loss = link_loss
        self.rns_message = rns_message
        self.links = LinkTracker(link_ifaces, link_hold_secs)
        self._neighbours_fn = neighbours_fn
        self._send_fn = send_fn
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stats_lock = threading.Lock()
        self.stats = {"queued": 0, "sent": 0, "failed": 0, "dropped_no_device": 0,
                      "dropped_queue_full": 0, "dropped_expired": 0,
                      "babel_read_failures": 0}
        self.last_error: str | None = None
        self.last_sent: float | None = None

    def _count(self, key: str) -> None:
        with self._stats_lock:
            self.stats[key] += 1

    # ── triggers ──
    def notify(self, kind: str, text: str, now: float | None = None) -> bool:
        """Queue a notification; False if the queue is full (dropped)."""
        try:
            self._q.put_nowait((time.time() if now is None else now, kind, text))
        except queue.Full:
            self._count("dropped_queue_full")
            log(f"queue full; dropped {kind} notification")
            return False
        self._count("queued")
        return True

    def on_rns_message(self, sender_name: str) -> None:
        """Inbound LXMF trigger. Sender only: message content never leaves LXMF."""
        if self.rns_message:
            self.notify("rns_message", f"Reticulum message from {sender_name}")

    # ── delivery ──
    def deliver(self, kind: str, text: str, now: float | None = None) -> int:
        """Send to every fresh local device; returns how many accepted it."""
        now = time.time() if now is None else now
        devices = self.devices.fresh(now)
        if not devices:
            self._count("dropped_no_device")
            log(f"no local ATAK device; dropped {kind} notification")
            return 0
        ok = 0
        for d in devices:
            data = build_geochat(self.sender_uid, self.callsign, d["uid"], text)
            dest = f"{d['callsign']} {d['ip']}:{d['port']}/{d['proto']}"
            try:
                self._send_fn(d["ip"], d["port"], d["proto"], data)
            except OSError as e:
                self._count("failed")
                self.last_error = f"{dest}: {e}"
                log(f"{kind} notification to {dest} failed: {e}")
                continue
            ok += 1
            self._count("sent")
            self.last_sent = now
            log(f"{kind} notification sent to {dest}")
        return ok

    def process_one(self, timeout: float = 1.0, now: float | None = None) -> bool:
        """Deliver one queued notification (dropped if expired). False if idle."""
        try:
            ts, kind, text = self._q.get(timeout=timeout)
        except queue.Empty:
            return False
        now = time.time() if now is None else now
        if now - ts > QUEUE_EXPIRY_SECS:
            self._count("dropped_expired")
            log(f"expired; dropped {kind} notification")
            return True
        self.deliver(kind, text, now)
        return True

    def _sender_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.process_one()

    # ── discovery ──
    def _open_sa_socket(self) -> socket.socket | None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Only this socket's own (br-lan) membership, not every group on the box.
            s.setsockopt(socket.IPPROTO_IP, IP_MULTICAST_ALL, 0)
            s.bind((SA_GROUP, SA_PORT))
            mreq = socket.inet_aton(SA_GROUP) + socket.inet_aton(self.br_lan_ip)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            s.settimeout(2.0)
            return s
        except OSError as e:
            log(f"SA listener on br-lan {self.br_lan_ip} not ready: {e}")
            s.close()
            return None

    def _discovery_loop(self, stop: threading.Event) -> None:
        sock = None
        while not stop.is_set():
            if sock is None:
                sock = self._open_sa_socket()
                if sock is None:
                    stop.wait(10)
                    continue
                log(f"listening for ATAK SA on {SA_GROUP}:{SA_PORT} via br-lan")
            try:
                data, addr = sock.recvfrom(SA_MAX_BYTES + 1)
            except socket.timeout:
                continue
            except OSError as e:
                log(f"SA listener error: {e}; reopening")
                sock.close()
                sock = None
                continue
            self.devices.observe(data, addr[0], time.time())
        if sock is not None:
            sock.close()

    # ── link monitor ──
    def poll_links(self, now: float | None = None) -> list[dict]:
        fn = self._neighbours_fn
        if fn is None:
            from ..status import babel_neighbours_checked as fn
        nbrs = fn()
        if nbrs is None:
            self._count("babel_read_failures")
        now = time.time() if now is None else now
        events = self.links.update(nbrs, now)
        for ev in events:
            self.notify(f"mesh_link_{ev['event']}", link_event_text(ev), now)
        return events

    def _link_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.poll_links()
            except Exception as e:  # one bad poll must not kill the monitor
                log(f"link poll error: {e}")
            stop.wait(LINK_POLL_SECS)

    def start(self, stop: threading.Event) -> None:
        for name, target in (("send", self._sender_loop), ("sa", self._discovery_loop)):
            threading.Thread(target=target, args=(stop,), name=f"atak-notify-{name}",
                             daemon=True).start()
        if self.link_loss:
            threading.Thread(target=self._link_loop, args=(stop,),
                             name="atak-notify-links", daemon=True).start()
        log(f"up as {self.callsign} ({self.sender_uid}); link_loss={self.link_loss} "
            f"ifaces={sorted(self.links.ifaces)} rns_message={self.rns_message}")

    def status(self) -> dict:
        now = time.time()
        with self._stats_lock:
            stats = dict(self.stats)
        return {
            "enabled": True,
            "sender_uid": self.sender_uid,
            "callsign": self.callsign,
            "link_loss": self.link_loss,
            "link_ifaces": sorted(self.links.ifaces),
            "rns_message": self.rns_message,
            "devices": [{"uid": d["uid"], "callsign": d["callsign"],
                         "endpoint": f"{d['ip']}:{d['port']}/{d['proto']}",
                         "age_secs": round(now - d["last_seen"], 1)}
                        for d in self.devices.fresh(now)],
            "stats": stats,
            "last_error": self.last_error,
            "last_sent": self.last_sent,
        }

