"""Pure helpers for the Reticulum/LXMF direct-message lane.

Everything in this module is side-effect free and unit-testable off-box: it has
no sockets, no threads, and does not require the ``rns``/``lxmf`` packages at
import time (the msgpack codec is imported lazily inside the two functions that
need it). The live stack wiring lives in ``rns_lane.py``; the app/aspect names,
the custom-announce wire format, and the peer bookkeeping are here so they can be
exercised with real announce bytes in tests.

Two destinations are announced from the one node identity:

  * ``lxmf.delivery`` — the standard LXMF inbox other apps (Sideband, MeshChat,
    NomadNet) and other nodes send to. Its app_data is owned by LXMF; we do not
    touch it.
  * ``nucleus.node`` — our own announce. Its app_data is a compact msgpack map
    (see ``schema.NucleusConfig.rns_announce_appdata``) so a peer can recognise
    a Nucleus node and learn its id/host/IPs/version/caps. Because both
    destinations derive from the same identity, a peer that hears a nucleus.node
    announce can compute that node's lxmf.delivery address directly and message
    it — that is how one node discovers another and knows where to send.
"""

from __future__ import annotations

import time
from typing import Optional

# Reticulum app/aspect names. The LXMF delivery aspect is fixed by the LXMF
# spec ("lxmf"/"delivery"); the nucleus.node pair is ours.
LXMF_APP_NAME = "lxmf"
LXMF_DELIVERY_ASPECT = "delivery"
NUCLEUS_APP_NAME = "nucleus"
NUCLEUS_NODE_ASPECT = "node"

# Soft ceiling for the custom app_data. A Reticulum packet is capped around 500
# bytes total; the announce also carries the public key, signature and name, so
# we keep our payload well under that. Enforced by encode_node_appdata.
MAX_APPDATA_BYTES = 300


def _umsgpack():
    """Lazy msgpack codec, reusing the one vendored in RNS when available.

    Falls back to the standalone ``umsgpack``/``msgpack`` so the pure functions
    remain usable (and testable) on hosts without the full RNS install.
    """
    try:
        import RNS.vendor.umsgpack as mp  # type: ignore
        return mp
    except Exception:  # pragma: no cover - exercised only off-box
        try:
            import umsgpack as mp  # type: ignore
            return mp
        except Exception:
            import msgpack as mp  # type: ignore
            return mp


def encode_node_appdata(appdata: dict) -> bytes:
    """Serialise the nucleus.node app_data map to compact msgpack bytes.

    Raises ValueError if the result exceeds MAX_APPDATA_BYTES so an over-large
    payload fails loudly at send time rather than silently truncating on the
    wire (which would corrupt the announce).
    """
    mp = _umsgpack()
    packed = mp.packb(appdata)
    if len(packed) > MAX_APPDATA_BYTES:
        raise ValueError(
            f"nucleus.node app_data is {len(packed)}B, over the "
            f"{MAX_APPDATA_BYTES}B limit; trim fields"
        )
    return packed


def parse_node_appdata(data: Optional[bytes]) -> Optional[dict]:
    """Parse nucleus.node announce app_data bytes back to a dict.

    Returns None for anything that is not a well-formed v1 payload (missing
    bytes, bad msgpack, not a dict, or a future/foreign ``v``) so a malformed or
    unknown announce can never crash the announce handler. Only ``v == 1`` is
    accepted here; newer versions are ignored until this code understands them.
    """
    if not data:
        return None
    mp = _umsgpack()
    try:
        obj = mp.unpackb(data)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("v") != 1:
        return None
    return obj


class PeerTable:
    """In-memory registry of discovered Nucleus nodes, keyed by node id.

    Pure data structure (no I/O): the lane feeds it parsed nucleus.node
    announces via ``update`` and the API reads ``as_list``. Each peer records
    its node metadata plus the lxmf.delivery destination hash to message it and
    a ``last_seen`` epoch so stale peers can be aged out / sorted by the UI.
    """

    def __init__(self) -> None:
        self._peers: dict[int, dict] = {}

    def update(self, appdata: dict, delivery_hash: str,
               hops: Optional[int] = None, ts: Optional[float] = None) -> dict:
        """Record (or refresh) a peer from a parsed nucleus.node announce.

        Returns the stored peer dict. ``delivery_hash`` is the hex lxmf.delivery
        address computed from the announcing identity; ``hops`` is the path cost
        if known. Re-announcing simply refreshes the entry and last_seen.
        """
        now = ts if ts is not None else time.time()
        nid = appdata.get("id")
        peer = {
            "id": nid,
            "host": appdata.get("host"),
            "mesh_ip": appdata.get("mesh_ip"),
            "br_lan": appdata.get("br_lan"),
            "sw": appdata.get("sw"),
            "caps": list(appdata.get("caps") or []),
            "lxmf_hash": delivery_hash,
            "hops": hops,
            "last_seen": now,
        }
        self._peers[nid] = peer
        return peer

    def as_list(self, max_age_secs: Optional[float] = None,
                now: Optional[float] = None) -> list[dict]:
        """Return known peers newest-seen first, optionally dropping stale ones."""
        cur = now if now is not None else time.time()
        out = []
        for peer in self._peers.values():
            if max_age_secs is not None and cur - peer["last_seen"] > max_age_secs:
                continue
            out.append(dict(peer))
        out.sort(key=lambda p: p["last_seen"], reverse=True)
        return out

    def get(self, node_id: int) -> Optional[dict]:
        peer = self._peers.get(node_id)
        return dict(peer) if peer else None
