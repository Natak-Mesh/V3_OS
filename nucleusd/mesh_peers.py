"""Shared WiFi-mesh peer discovery over Babel + per-peer web fetches.

Every Nucleus node runs the same nucleusd web app, so one node can learn things
about its neighbours by (1) finding their mesh IPs from Babel's kernel routes and
(2) fetching a known endpoint from each over HTTP on the web port. This was first
written for Meshtastic channel sharing; it is factored out here so the Reticulum
contact-card exchange (and anything else) reuses exactly the same discovery path
instead of duplicating the Babel parsing.

Pure-ish: ``babel_peer_ips`` shells out to ``ip route`` (read-only), and
``fetch_peer_json`` does a short-timeout HTTP GET. Neither mutates node state.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from typing import Optional

# The nucleusd web app (this API's host) listens here on every node.
WEB_PORT = 8080
# Per-peer HTTP timeout when polling neighbours — kept short so a slow/absent
# node never stalls a discovery sweep.
PEER_HTTP_TIMEOUT = 3


def babel_peer_ips(dev: str = "wlan1") -> list[str]:
    """Return mesh node IPs discovered from Babel routes on ``dev``.

    ``ip route show proto babel`` lists next-hop IPs, each a reachable mesh node
    running nucleusd. Never raises — returns [] on any error.
    """
    ips: list[str] = []
    try:
        result = subprocess.run(
            ["ip", "route", "show", "proto", "babel", "dev", dev],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            m = re.search(r"via\s+(\S+)", line)
            if m and m.group(1) not in ips:
                ips.append(m.group(1))
    except Exception:
        pass
    return ips


def fetch_peer_json(ip: str, path: str,
                    timeout: int = PEER_HTTP_TIMEOUT) -> Optional[dict]:
    """GET ``http://<ip>:8080<path>`` and return parsed JSON, or None on error.

    Best-effort and never raises, so callers can fan this out across neighbours
    and simply skip the ones that don't answer.
    """
    try:
        url = f"http://{ip}:{WEB_PORT}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
