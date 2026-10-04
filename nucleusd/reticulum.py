"""Read-only Reticulum (rnsd) status — a thin wrapper over the shared instance.

rnsd runs as user 'natak' and, with `share_instance = Yes`, exposes a local RPC
control socket (the same one `rnstatus`/`rnpath` use). On Linux that socket is an
abstract AF_UNIX endpoint named "\\0rns/<instance>/rpc", authenticated with a key
derived from the running transport identity:

    rpc_key = SHA-256( <64-byte private key in storage/transport_identity> )

We talk to it directly with the stdlib `multiprocessing.connection` client and
msgpack, so this module needs neither a running RNS stack nor any extra memory
(a first-class concern on 1 GB nodes) — it just opens a socket, sends one
request, and parses the reply.

Deliberately outside the config.yaml -> apply pipeline: this is pure runtime
status, like status.py. Every function returns plain data (or raises
ReticulumError), so the router/tests can exercise it with a mocked `_rpc`.
"""

from __future__ import annotations

import hashlib
import multiprocessing.connection as mpc
from pathlib import Path

# NOTE: RNS is imported lazily inside _rpc (not at module load) so this module
# stays importable on off-box dev/test hosts where the rns package isn't
# installed. Only msgpack framing is needed, and only when actually talking to
# rnsd — the parsing/formatting helpers below are pure and fully unit-testable.

# rnsd runs as 'natak'; its storage (and the transport identity that seeds the
# RPC key) lives under that home. The instance name is the rnsd default.
RETI_USER = "natak"
TRANSPORT_IDENTITY = Path(f"/home/{RETI_USER}/.reticulum/storage/transport_identity")
INSTANCE_NAME = "default"
# Abstract AF_UNIX address (leading NUL) — matches Reticulum.rpc_addr on Linux.
RPC_ADDR = f"\0rns/{INSTANCE_NAME}/rpc"
_TIMEOUT = 5.0

# RNS interface mode byte -> human label (RNS.Interfaces.Interface.Interface).
_MODES = {
    0x01: "full",
    0x02: "pointtopoint",
    0x03: "accesspoint",
    0x04: "roaming",
    0x05: "boundary",
    0x06: "gateway",
    0x07: "internal",
}

# Interface name prefixes that are spawned per-peer/per-client. rnstatus hides
# these by default; we do the same so the page shows configured interfaces only.
_EPHEMERAL_PREFIXES = (
    "LocalInterface[",
    "TCPInterface[Client",
    "BackboneInterface[Client on",
    "AutoInterfacePeer[",
    "WeaveInterfacePeer[",
    "I2PInterfacePeer[Connected peer",
)


class ReticulumError(RuntimeError):
    """The rnsd RPC socket was unreachable or returned an unusable reply."""


def _rpc_key() -> bytes:
    """Derive the shared-instance RPC authkey from the transport identity.

    Mirrors Reticulum: SHA-256 of the 64-byte private key on disk. Raises
    ReticulumError if the identity file is missing (rnsd never started).
    """
    try:
        return hashlib.sha256(TRANSPORT_IDENTITY.read_bytes()).digest()
    except OSError as e:
        raise ReticulumError(f"transport identity unreadable: {e}") from e


def _rpc(request: dict, timeout: float = _TIMEOUT) -> object:
    """Send one request dict to the rnsd control socket and return the reply.

    One connection per call (the control socket is request/response, not a
    stream). Any socket/auth error is surfaced as ReticulumError so the router
    can translate it to HTTP 503.
    """
    import RNS.vendor.umsgpack as mp  # lazy: see module docstring note above

    key = _rpc_key()
    try:
        conn = mpc.Client(RPC_ADDR, family="AF_UNIX", authkey=key)
    except (OSError, EOFError) as e:
        raise ReticulumError(f"rnsd control socket unreachable: {e}") from e
    try:
        conn.send_bytes(mp.packb(request))
        return mp.unpackb(conn.recv_bytes())
    except (OSError, EOFError) as e:
        raise ReticulumError(f"rnsd RPC failed: {e}") from e
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hexrep(value: object) -> str | None:
    """Render a bytes hash as a lowercase hex string; pass through None."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return None


def _is_ephemeral(name: str) -> bool:
    return name.startswith(_EPHEMERAL_PREFIXES)


def _iface_summary(ifstat: dict) -> dict:
    """Project one raw interface-stats entry to the stable UI contract."""
    name = ifstat.get("name", "")
    clients = ifstat.get("clients")
    # The shared instance counts rnsd itself as a client; report attached
    # programs the way rnstatus does (clients - 1, floored at 0).
    if name.startswith("Shared Instance[") and isinstance(clients, int):
        clients = max(clients - 1, 0)
    return {
        "name": name,
        "short_name": ifstat.get("short_name"),
        "up": bool(ifstat.get("status")),
        "mode": _MODES.get(ifstat.get("mode"), "full"),
        "bitrate": ifstat.get("bitrate"),
        "rxb": ifstat.get("rxb", 0),
        "txb": ifstat.get("txb", 0),
        "rxs": ifstat.get("rxs", 0.0),
        "txs": ifstat.get("txs", 0.0),
        "announces_in": ifstat.get("incoming_announce_frequency", 0.0),
        "announces_out": ifstat.get("outgoing_announce_frequency", 0.0),
        "clients": clients,
    }



def _transport_info(stats: dict) -> dict:
    """Project transport-wide identity/uptime/throughput totals."""
    return {
        "transport_id": _hexrep(stats.get("transport_id")),
        "uptime": stats.get("transport_uptime"),
        "rxb": stats.get("rxb", 0),
        "txb": stats.get("txb", 0),
        "rxs": stats.get("rxs", 0.0),
        "txs": stats.get("txs", 0.0),
    }


def interfaces(include_ephemeral: bool = False) -> list[dict]:
    """Return the configured interfaces with traffic/announce/client counters.

    Per-peer spawned interfaces are hidden unless include_ephemeral is set,
    matching `rnstatus` default behaviour.
    """
    stats = _rpc({"get": "interface_stats"})
    if not isinstance(stats, dict) or "interfaces" not in stats:
        raise ReticulumError("malformed interface_stats reply")
    out = []
    for ifstat in stats["interfaces"]:
        if include_ephemeral or not _is_ephemeral(ifstat.get("name", "")):
            out.append(_iface_summary(ifstat))
    return out


def transport() -> dict:
    """Return transport-wide identity/uptime/throughput totals."""
    stats = _rpc({"get": "interface_stats"})
    if not isinstance(stats, dict):
        raise ReticulumError("malformed interface_stats reply")
    return _transport_info(stats)


def path_table(max_hops: int | None = None) -> list[dict]:
    """Return known destination paths (hash, next-hop, hops, interface, age)."""
    table = _rpc({"get": "path_table", "max_hops": max_hops})
    if not isinstance(table, list):
        raise ReticulumError("malformed path_table reply")
    out = []
    for entry in table:
        out.append({
            "hash": _hexrep(entry.get("hash")),
            "via": _hexrep(entry.get("via")),
            "hops": entry.get("hops"),
            "interface": entry.get("interface"),
            "timestamp": entry.get("timestamp"),
            "expires": entry.get("expires"),
        })
    return out


def status() -> dict:
    """Full read-only snapshot for the API/UI.

    Shape (stable contract for the UI): running, transport{...},
    interfaces[...], path_count. `running` is False (rather than an error) when
    rnsd is simply not up, so the page can render a clean "stopped" state.
    """
    try:
        stats = _rpc({"get": "interface_stats"})
    except ReticulumError:
        return {"running": False, "transport": {}, "interfaces": [], "path_count": 0}

    if not isinstance(stats, dict) or "interfaces" not in stats:
        raise ReticulumError("malformed interface_stats reply")

    ifaces = [
        _iface_summary(i) for i in stats["interfaces"]
        if not _is_ephemeral(i.get("name", ""))
    ]
    try:
        path_count = len(path_table())
    except ReticulumError:
        path_count = 0

    return {
        "running": True,
        "transport": _transport_info(stats),
        "interfaces": ifaces,
        "path_count": path_count,
    }
