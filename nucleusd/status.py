"""Runtime status collection for the API and CLI.

Read-only: queries babeld (via its local monitor port), interface addresses, and
systemd unit states. Never mutates the system.
"""

from __future__ import annotations

import socket
import subprocess


def babel_neighbours(port: int = 33123, timeout: float = 2.0) -> list[dict]:
    """Parse Babel's local read-only monitor socket for current neighbours.

    babeld exposes a plaintext dump on local-port (33123 in our babeld.conf).
    We connect, read the initial state dump, and extract neighbour lines.
    """
    neighbours: list[dict] = []
    try:
        with socket.create_connection(("::1", port), timeout=timeout) as s:
            s.settimeout(timeout)
            buf = b""
            while b"\n" in buf or len(buf) < 65536:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                if b"ok" in buf.split(b"\n")[-2:][0]:
                    break
    except (OSError, IndexError):
        return neighbours

    for line in buf.decode(errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "add" and parts[1] == "neighbour":
            entry = {"id": parts[2]}
            for i in range(3, len(parts) - 1, 2):
                entry[parts[i]] = parts[i + 1]
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
    }
