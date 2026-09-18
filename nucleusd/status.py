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


def iface_addrs(names: tuple[str, ...] = ("wlan1", "br-lan", "wlan0", "eth0")) -> dict:
    """Return {iface: {state, addrs[]}} using `ip -o addr`."""
    out: dict[str, dict] = {n: {"state": "absent", "addrs": []} for n in names}
    try:
        r = subprocess.run(["ip", "-o", "addr", "show"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return out
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        name = f[1]
        if name in out:
            out[name]["state"] = "up"
            if f[2] in ("inet", "inet6"):
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
