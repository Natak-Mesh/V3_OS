"""Runtime status collection for the API and CLI.

Read-only: queries babeld (via its local monitor port), interface addresses, and
systemd unit states. Never mutates the system.
"""

from __future__ import annotations

import socket
import subprocess


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
        direct = refmetric == "0"
        routes.append({
            "node": f"10.20.1.{octets[2]}",
            "via": None if direct else via_to_ipv4.get(via, via),
            "metric": metric_int,
            "direct": direct,
        })
    routes.sort(key=lambda r: r["node"])
    return routes


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
            out[name]["oper_state"] = f[f.index("state") + 1]
        if "master" in f:
            out[name]["master"] = f[f.index("master") + 1]


def iface_addrs(names: tuple[str, ...] = ("wlan1", "br-lan", "wlan0", "eth0")) -> dict:
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


def collect() -> dict:
    return {
        "interfaces": iface_addrs(),
        "services": unit_states(),
        "babel_neighbours": babel_neighbours(),
        "babel_routes": babel_routes(),
        "meshtastic": {"services": unit_states(
            ("meshtasticd", "cot-bridge", "nucleus-meshtastic-init"))},
    }
