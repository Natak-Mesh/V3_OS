# Nucleus control plugin for rnsd (RNS external interface module).
#
# Installed by install.sh to ~natak/.reticulum/interfaces/ and loaded by rnsd via
# the [[Nucleus Control]] entry in the rendered Reticulum config. rnsd exec()s
# this file with `Interface` and `RNS` in its globals and instantiates
# `interface_class`.
#
# It is NOT a link and never transmits on its own (outgoing = False,
# process_outgoing is a no-op). It exists because the RNode / modem73 radio
# interfaces run in access_point mode, where rnsd blocks every broadcast
# announce. An announce with attached_interface set passes that block, but only
# code inside rnsd can name an rnsd interface — shared-instance clients
# (nucleus-messaging) cannot. So this plugin listens on a local socket and, on
# request, re-sends the announce rnsd already holds for a destination on ONE
# named interface.
#
# Protocol: one JSON line per connection on the abstract AF_UNIX socket
# CTL_ADDR. Request {"cmd":"announce_on","interface":"<name>","dest":"<hex>"};
# reply {"ok":true} or {"ok":false,"error":"..."}.
#
# Depends on RNS 1.5.x internals: Transport.path_table entry layout
# (hops at [2], announce packet hash at [6]) and Transport.get_cached_packet.

import json
import socket
import threading

# Must match nucleusd.reticulum.CTL_ADDR (pinned by tests).
CTL_ADDR = "\0nucleus/rns-ctl"

IDX_PT_HOPS = 2
IDX_PT_PACKET = 6


def resend_announce(dest_hex, iface_name):
    """Re-send rnsd's stored announce for dest_hex on the interface named
    iface_name only. Returns (ok, error)."""
    try:
        dest_hash = bytes.fromhex(dest_hex)
    except (ValueError, TypeError):
        return False, "bad destination hash"

    target = None
    for iface in list(RNS.Transport.interfaces):
        if getattr(iface, "name", None) == iface_name and getattr(iface, "parent_interface", None) is None:
            target = iface
            break
    if target is None:
        return False, f"interface {iface_name!r} not configured"
    if not target.online:
        return False, f"interface {iface_name!r} is down"

    entry = RNS.Transport.path_table.get(dest_hash)
    if entry is None:
        return False, "no announce held for destination"
    cached = RNS.Transport.get_cached_packet(entry[IDX_PT_PACKET], packet_type="announce")
    if cached is None or not cached.unpack():
        return False, "stored announce unreadable"
    identity = RNS.Identity.recall(dest_hash)
    if identity is None:
        return False, "identity for destination unknown"

    # Same construction rnsd uses when it retransmits a local client's announce,
    # but attached to one interface so the access_point block does not apply.
    dest = RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "unknown", "unknown")
    dest.hash = dest_hash
    dest.hexhash = dest_hash.hex()
    packet = RNS.Packet(dest, cached.data, RNS.Packet.ANNOUNCE,
                        context=RNS.Packet.NONE, header_type=RNS.Packet.HEADER_2,
                        transport_type=RNS.Transport.TRANSPORT,
                        transport_id=RNS.Transport.identity.hash,
                        attached_interface=target, context_flag=cached.context_flag)
    packet.hops = entry[IDX_PT_HOPS]
    if packet.send() is False:
        return False, "transmit failed"
    RNS.log(f"Nucleus Control: announced {RNS.prettyhexrep(dest_hash)} on {target}", RNS.LOG_NOTICE)
    return True, None


def handle_request(raw):
    try:
        req = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": False, "error": "bad json"}
    if not isinstance(req, dict) or req.get("cmd") != "announce_on":
        return {"ok": False, "error": "unknown cmd"}
    try:
        ok, err = resend_announce(req.get("dest"), req.get("interface"))
    except Exception as e:
        return {"ok": False, "error": f"announce failed: {e}"}
    return {"ok": True} if ok else {"ok": False, "error": err}


class NucleusCtlInterface(Interface):
    def __init__(self, owner, configuration, addr=CTL_ADDR):
        super().__init__()
        c = Interface.get_config_obj(configuration)
        self.name = c["name"]
        self.owner = owner
        self.IN = False
        self.OUT = False
        self.bitrate = 1_000_000_000   # never the "slowest link" in RNS timing
        self.addr = addr
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(addr)
        self._sock.listen(4)
        self.online = True
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5.0)
                try:
                    buf = b""
                    while not buf.endswith(b"\n") and len(buf) < 4096:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    reply = handle_request(buf.strip())
                    conn.sendall(json.dumps(reply).encode("utf-8") + b"\n")
                except OSError:
                    pass

    def process_outgoing(self, data):
        pass   # never a link; rnsd may still hand it raw broadcasts

    def detach(self):
        self.online = False
        self.detached = True
        try:
            self._sock.close()
        except OSError:
            pass

    def __str__(self):
        return f"NucleusCtlInterface[{self.name}]"


interface_class = NucleusCtlInterface
