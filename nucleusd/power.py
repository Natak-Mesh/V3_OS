"""Node power control: reboot / power off.

Thin imperative layer (like update.py): the router/API only relays to these
functions, all logic lives here. nucleusd runs as root, so systemctl is invoked
directly (no sudo needed).

`systemctl reboot` / `poweroff` return immediately (they queue the transition
with systemd and detach), so the HTTP response is sent before the node actually
goes down.
"""

from __future__ import annotations

import subprocess


def _systemctl(action: str) -> dict:
    """Run `systemctl <action>`; never raise, report outcome as a dict."""
    r = subprocess.run(
        ["systemctl", action],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return {"started": False, "detail": (r.stderr or f"systemctl {action} failed").strip()}
    return {"started": True}


def reboot() -> dict:
    """Reboot the node."""
    return _systemctl("reboot")


def poweroff() -> dict:
    """Power off the node."""
    return _systemctl("poweroff")
