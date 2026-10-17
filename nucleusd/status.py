"""Runtime status collection for the API and CLI.

Read-only: queries babeld (via its local monitor port), interface addresses, and
systemd unit states. Never mutates the system.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time


def _babel_dump(port: int = 33123, timeout: float = 2.0) -> bytes:
    """Fetch Babel's read-only state dump from its local monitor socket.

    babeld exposes a plaintext dump on local-port (33123 in our babeld.conf).
    It emits a header ending in "ok" on connect, then stays quiet until given a
    command. We send "dump" to get the state dump (interfaces/neighbours/routes),
    which ends in a second "ok". Returns b"" on any connection error.
    """
    try:
        with socket.create_connection(("::1", port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"dump\n")
            buf = b""
            oks = 0
            while oks < 2 and len(buf) < 262144:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                # Count completed "ok" lines: header ok + end-of-dump ok.
                oks = sum(1 for ln in buf.split(b"\n") if ln.strip() == b"ok")
            return buf
    except OSError:
        return b""


def _via_to_ipv4(buf: bytes) -> dict[str, str]:
    """Map each neighbour's link-local IPv6 -> its mesh IPv4 (10.20.1.<id>).

    A neighbour advertises its br-lan /24 (10.20.<id>.0/24) via its link-local
    address. Only the origin advertises refmetric 0; relayed (multi-hop) routes
    share the same "via" but carry refmetric > 0, so keying on `via` alone would
    collapse multiple neighbours onto one IPv4. We accept only originated routes.
    """
    via_to_ipv4: dict[str, str] = {}
    for line in buf.decode(errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "add" and parts[1] == "route":
            try:
                prefix = parts[parts.index("prefix") + 1]
                via = parts[parts.index("via") + 1]
                refmetric = parts[parts.index("refmetric") + 1]
            except (ValueError, IndexError):
                continue
            if refmetric != "0":
                continue
            octets = prefix.split("/")[0].split(".")
            if len(octets) == 4 and octets[0] == "10" and octets[1] == "20" \
                    and octets[3] == "0" and octets[2] not in ("1", "0"):
                via_to_ipv4[via] = f"10.20.1.{octets[2]}"
    return via_to_ipv4


def babel_routes(port: int = 33123, timeout: float = 2.0) -> list[dict]:
    """Parse installed Babel routes to every reachable mesh node.

    Unlike babel_neighbours (1-hop only), this covers the whole mesh: one row
    per node's br-lan /24 (10.20.<id>.0/24) that Babel has selected (installed).
    Returns node IPv4, next-hop IPv4 (or None if direct), and raw metric.
    """
    routes: list[dict] = []
    buf = _babel_dump(port, timeout)
    if not buf:
        return routes
    via_to_ipv4 = _via_to_ipv4(buf)
    for line in buf.decode(errors="replace").splitlines():
        parts = line.split()
        if not (len(parts) >= 4 and parts[0] == "add" and parts[1] == "route"):
            continue
        try:
            prefix = parts[parts.index("prefix") + 1]
            via = parts[parts.index("via") + 1]
            metric = parts[parts.index("metric") + 1]
            installed = parts[parts.index("installed") + 1]
            refmetric = parts[parts.index("refmetric") + 1]
        except (ValueError, IndexError):
            continue
        if installed != "yes":
            continue
        # Only mesh node br-lan prefixes (10.20.<id>.0/24), excluding self(1)/0.
        octets = prefix.split("/")[0].split(".")
        if not (len(octets) == 4 and octets[0] == "10" and octets[1] == "20"
                and octets[3] == "0" and octets[2] not in ("1", "0")):
            continue
        try:
            metric_int = int(metric)
        except ValueError:
            continue
        try:
            iface = parts[parts.index("if") + 1]
        except (ValueError, IndexError):
            iface = None
        direct = refmetric == "0"
        routes.append({
            "node": f"10.20.1.{octets[2]}",
            "via": None if direct else via_to_ipv4.get(via, via),
            "metric": metric_int,
            "direct": direct,
            "iface": iface,
        })
    routes.sort(key=lambda r: r["node"])
    return routes


def _halow_neigh_mac_to_ipv6() -> dict[str, str]:
    """Map each halow0 neighbour's MAC -> its link-local IPv6 (fe80::…).

    `ip -6 neigh show dev halow0` lists the IPv6 (incl. link-locals Babel peers
    on) against the MAC the radio reports in `iw station dump`. We prefer the
    link-local because that is the address Babel keys neighbours by, letting us
    join the radio-link view to the routing view. Best-effort: {} on any error.
    """
    out: dict[str, str] = {}
    try:
        r = subprocess.run(["ip", "-6", "neigh", "show", "dev", "halow0"],
                           capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return out
    for line in r.stdout.splitlines():
        f = line.split()
        # e.g.: "fe80::b7dd:6ba5:30a1:8929 lladdr 0c:bf:74:00:29:07 router STALE"
        if len(f) >= 3 and f[1] == "lladdr":
            ipv6, mac = f[0], f[2].lower()
            # Prefer a link-local (fe80::/10) over any global for the same MAC.
            if mac not in out or ipv6.lower().startswith("fe80"):
                out[mac] = ipv6
    return out


def halow_peers(port: int = 33123, timeout: float = 2.0) -> list[dict]:
    """HaLow (halow0) 802.11s peers, one row per linked station.

    Babel's route list cannot show HaLow-only neighbours when the same node is
    cheaper over wlan1, so this reports the radio link directly. It joins three
    read-only sources:
      * `iw dev halow0 station dump` — MAC, mesh plink state, avg signal (dBm),
        inactivity (ms since last frame) and connected time (s).
      * `ip -6 neigh show dev halow0` — MAC -> link-local IPv6.
      * Babel neighbours seen on halow0 — link % and cost, keyed by that IPv6,
        plus the node's canonical mesh IPv4 (10.20.1.<id>) when resolvable.

    Returns [] when halow0 is absent or iw is unavailable; never raises.
    """
    peers: list[dict] = []
    try:
        r = subprocess.run(["iw", "dev", "halow0", "station", "dump"],
                           capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return peers
    if r.returncode != 0:
        return peers

    mac_to_ipv6 = _halow_neigh_mac_to_ipv6()
    # Babel neighbours on halow0, keyed by their link-local IPv6.
    babel_by_ipv6: dict[str, dict] = {
        n["address"]: n for n in babel_neighbours(port, timeout)
        if n.get("if") == "halow0" and n.get("address")
    }

    cur: dict | None = None
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith("Station "):
            if cur is not None:
                peers.append(cur)
            cur = {"mac": s.split()[1].lower()}
        elif cur is None:
            continue
        elif s.startswith("inactive time:"):
            cur["inactive_ms"] = _int_before(s, "ms")
        elif s.startswith("signal avg:"):
            cur["signal_avg_dbm"] = _int_before(s, "dBm")
        elif s.startswith("connected time:"):
            cur["connected_s"] = _int_before(s, "seconds")
        elif s.startswith("mesh plink:"):
            cur["plink"] = s.split(":", 1)[1].strip()
    if cur is not None:
        peers.append(cur)

    for p in peers:
        ipv6 = mac_to_ipv6.get(p["mac"])
        p["ipv6"] = ipv6
        nb = babel_by_ipv6.get(ipv6) if ipv6 else None
        p["link_pct"] = nb.get("link_pct") if nb else None
        p["ipv4"] = nb.get("ipv4") if nb else None
        try:
            p["cost"] = int(nb["cost"]) if nb and "cost" in nb else None
        except (ValueError, TypeError):
            p["cost"] = None
    peers.sort(key=lambda p: p.get("ipv4") or p["mac"])
    return peers


def _int_before(line: str, unit: str) -> int | None:
    """Return the integer token immediately before `unit` in `line`, or None.

    Tolerant of the `iw station dump` layout ("signal avg:\t-5 dBm") where the
    value and its unit are the last two whitespace-separated tokens.
    """
    toks = line.replace("\t", " ").split()
    if unit in toks:
        i = toks.index(unit)
        if i > 0:
            try:
                return int(toks[i - 1])
            except ValueError:
                return None
    return None


def babel_neighbours(port: int = 33123, timeout: float = 2.0) -> list[dict]:
    """Parse Babel's local read-only monitor socket for current neighbours."""
    neighbours: list[dict] = []
    buf = _babel_dump(port, timeout)
    if not buf:
        return neighbours

    via_to_ipv4 = _via_to_ipv4(buf)
    for line in buf.decode(errors="replace").splitlines():
        parts = line.split()
        # Format: add neighbour <id> address <ip> if <iface> reach <hex>
        #         ureach <hex> rxcost <n> txcost <n> cost <n>
        if len(parts) >= 4 and parts[0] == "add" and parts[1] == "neighbour":
            entry = {"id": parts[2]}
            for i in range(3, len(parts) - 1, 2):
                entry[parts[i]] = parts[i + 1]
            # Resolve a human-usable IPv4 from the neighbour's advertised route.
            entry["ipv4"] = via_to_ipv4.get(entry.get("address", ""))
            # reach is a 16-bit hex history of recent hellos; expose it as a
            # link-quality percentage (bits set / 16) instead of raw hex.
            try:
                bits = bin(int(entry.get("reach", "0"), 16)).count("1")
                entry["link_pct"] = round(bits / 16 * 100)
            except ValueError:
                entry["link_pct"] = None
            neighbours.append(entry)
    return neighbours


def _parse_link(out: dict) -> None:
    """Fill oper-state and bridge master from `ip -o link show`.

    Existence/state MUST come from the link table, not the addr table: a bridge
    member like wlan0 (AP enslaved to br-lan) carries no IP, so it never appears
    in `ip addr` and was wrongly reported "absent".
    """
    try:
        r = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return
    for line in r.stdout.splitlines():
        f = line.split()
        # e.g.: "3: wlan0: <...,UP,LOWER_UP> mtu 1500 ... master br-lan state UP ..."
        if len(f) < 2:
            continue
        name = f[1].rstrip(":")
        if name not in out:
            continue
        out[name]["state"] = "present"
        if "state" in f:
            oper = f[f.index("state") + 1]
            # 802.11s mesh interfaces sit in DORMANT forever by kernel design,
            # even while fully associated and routing. The link is genuinely
            # carrying traffic when LOWER_UP is set in the flags field (f[2],
            # e.g. "<...,UP,LOWER_UP>"), so report that as UP; a truly down
            # radio lacks LOWER_UP and stays DORMANT.
            if oper == "DORMANT" and len(f) > 2 and "LOWER_UP" in f[2]:
                oper = "UP"
            out[name]["oper_state"] = oper
        if "master" in f:
            out[name]["master"] = f[f.index("master") + 1]


def iface_addrs(names: tuple[str, ...] = ("wlan1", "halow0", "br-lan", "wlan0", "eth0")) -> dict:
    """Return {iface: {state, oper_state, master?, addrs[]}}.

    Merges `ip -o link` (existence/state/bridge master) with `ip -o addr`
    (addresses) so address-less bridge members are reported correctly.
    """
    out: dict[str, dict] = {n: {"state": "absent", "addrs": []} for n in names}
    _parse_link(out)
    try:
        r = subprocess.run(["ip", "-o", "addr", "show"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return out
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        name = f[1]
        if name in out and f[2] in ("inet", "inet6"):
            out[name]["addrs"].append(f[3])
    return out


def unit_states(units: tuple[str, ...] = ("systemd-networkd", "nucleus-mesh", "babeld", "smcroute", "hostapd")) -> dict:
    """Return {unit: active|inactive|failed|...} via systemctl is-active."""
    states: dict[str, str] = {}
    for u in units:
        r = subprocess.run(["systemctl", "is-active", f"{u}.service"], capture_output=True, text=True, check=False)
        states[u] = r.stdout.strip() or "unknown"
    return states


# ── Mesh node last-seen tracker ────────────────────────────────
# Babel only reports who is reachable *now*; it keeps no history. This records
# when each mesh node (direct or multi-hop) was last in the installed route
# table, so a lost node stays listed with its age instead of vanishing.
# In-memory only: nucleusd's background thread feeds it every MESH_POLL_S, and
# every collect() folds in its own snapshot too.
MESH_POLL_S = 5
MESH_MAX_AGE_S = 900  # 15 min, same as the LoRa node list

_mesh_lock = threading.Lock()
_mesh_nodes: dict[str, dict] = {}
_mesh_started = False


def _mesh_update(routes: list[dict], now: float) -> None:
    """Fold one babel_routes() snapshot into the last-seen table.

    Nodes in the snapshot are reachable and seen `now`. Nodes missing from it
    are lost (including when babeld is unreachable: no babel, no connection) and
    keep their last route + time until MESH_MAX_AGE_S, then are dropped.
    """
    with _mesh_lock:
        present = set()
        for r in routes:
            _mesh_nodes[r["node"]] = {**r, "reachable": True, "last_seen": now}
            present.add(r["node"])
        for node, n in list(_mesh_nodes.items()):
            if node in present:
                continue
            if now - n["last_seen"] > MESH_MAX_AGE_S:
                del _mesh_nodes[node]
            else:
                n["reachable"] = False


def mesh_nodes() -> list[dict]:
    """Every mesh node seen in the last MESH_MAX_AGE_S, sorted by node IP.

    Each entry: node, via, metric, direct (last known route), reachable,
    last_seen (epoch seconds).
    """
    with _mesh_lock:
        return sorted((dict(n) for n in _mesh_nodes.values()), key=lambda n: n["node"])


def _mesh_loop() -> None:
    while True:
        try:
            _mesh_update(babel_routes(), time.time())
        except Exception:  # never let one bad poll kill the tracker
            pass
        time.sleep(MESH_POLL_S)


def start_mesh_tracker() -> None:
    """Start the background last-seen poller (idempotent). Called by nucleusd."""
    global _mesh_started
    with _mesh_lock:
        if _mesh_started:
            return
        _mesh_started = True
    threading.Thread(target=_mesh_loop, name="mesh-tracker", daemon=True).start()


def radios() -> dict:
    """Per-radio on/off state for the Radios UI page (Phase 4/5).

    Combines the config source-of-truth with live hardware/unit state:
      * mesh (wlan1) — config mesh.enabled + live phy1 rfkill soft-block.
      * meshtastic   — config tx_enabled (receive-only when false) + meshtasticd unit.
      * ap (wlan0)   — live only (outside the pipeline): phy0 rfkill + hostapd unit.

    Each radio reports `on` as the operator-facing state. `rf_blocked` is the live
    rfkill view (None = unknown, e.g. off-box or phy absent). All best-effort:
    config-load or rfkill failures degrade to None rather than raising.
    """
    from . import rf
    try:
        from . import config as cfgio
        cfg = cfgio.load()
    except Exception:  # noqa: BLE001 — status must never raise
        cfg = None

    def _blocked(iface: str):
        phy = rf.phy_for_iface(iface)
        return rf.is_blocked(phy) if phy else None

    units = unit_states(("meshtasticd", "hostapd", "nucleus-halow"))
    mesh_enabled = cfg.mesh.enabled if cfg else None
    tx_enabled = cfg.meshtastic.tx_enabled if cfg else None
    ap_blocked = _blocked(rf.AP_IFACE)

    # HaLow: operator-facing on = config halow.enabled AND the halow0 interface is
    # live. Report the REAL S1G channel/bandwidth from config (the driver only ever
    # shows the 5 GHz stand-ins to iw — see docs/halow.md §6), never a stand-in.
    halow_enabled = cfg.halow.enabled if cfg else None
    halow_present = iface_addrs(("halow0",))["halow0"].get("state") != "absent"

    return {
        "mesh": {
            "on": mesh_enabled,
            "rf_blocked": _blocked(rf.MESH_IFACE),
            "persistent": True,
        },
        "halow": {
            # on = enabled in config AND the radio's interface exists.
            "on": (bool(halow_enabled) and halow_present) if cfg else None,
            "enabled": halow_enabled,
            "present": halow_present,
            "channel": cfg.halow.channel if cfg else None,
            "bandwidth_mhz": cfg.halow.bandwidth_mhz if cfg else None,
            "freq_mhz": cfg.halow_freq_mhz if cfg else None,
            "service": units.get("nucleus-halow"),
            "persistent": True,
        },
        "meshtastic": {
            # "on" here means TX allowed; off = receive-only (radio still RX's).
            "on": tx_enabled,
            "receive_only": (tx_enabled is False),
            "daemon": units.get("meshtasticd"),
            "persistent": True,
        },
        "ap": {
            # Live only: on when hostapd is active AND phy0 isn't soft-blocked.
            "on": (units.get("hostapd") == "active" and ap_blocked is False),
            "rf_blocked": ap_blocked,
            "hostapd": units.get("hostapd"),
            "persistent": False,  # resets ON at boot (README §4)
        },
    }


def collect() -> dict:
    routes = babel_routes()
    _mesh_update(routes, time.time())
    return {
        "interfaces": iface_addrs(),
        "services": unit_states(),
        "babel_neighbours": babel_neighbours(),
        "babel_routes": routes,
        "mesh_nodes": mesh_nodes(),
        "halow_peers": halow_peers(),
        "meshtastic": {"services": unit_states(
            ("meshtasticd", "cot-bridge", "nucleus-meshtastic-init"))},
        "radios": radios(),
    }
