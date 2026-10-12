#!/usr/bin/env python3
"""First-boot Meshtastic radio configuration.

Runs once after meshtasticd is up (nucleus-meshtastic-init.service) to apply
the two settings the radio can't derive itself:

  * owner / owner-short  — set from the node hostname every boot (cheap, no
    reboot), so the mesh shows human-readable node names.
  * lora.region          — set only if still UNSET; without a region the radio
    refuses to transmit ("lora tx disabled: Region unset"). Setting it reboots
    the radio, so we do it only on first boot.

Reads the desired values from /etc/nucleus/config.yaml. Best-effort: logs and
exits 0 even on failure so it never blocks boot.
"""

from __future__ import annotations

import subprocess
import sys
import time

from .. import config as cfgio

HOST = "localhost"
MESHTASTIC = [sys.executable, "-m", "meshtastic", "--host", HOST]


def _cli(args: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(MESHTASTIC + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001 — best-effort init
        return -1, str(e)


def _wait_for_api(attempts: int = 30, delay: int = 2) -> bool:
    for _ in range(attempts):
        rc, _out = _cli(["--info"], timeout=15)
        if rc == 0:
            return True
        time.sleep(delay)
    return False


def main() -> None:
    try:
        cfg = cfgio.load()
    except Exception as e:  # noqa: BLE001
        print(f"radio_init: cannot load config: {e}", file=sys.stderr)
        return
    if not cfg.meshtastic.enabled:
        print("radio_init: meshtastic disabled — nothing to do")
        return

    if not _wait_for_api():
        print("radio_init: meshtasticd API never came up — skipping", file=sys.stderr)
        return

    # Owner name every boot (cheap, no radio reboot).
    _cli(["--set-owner", cfg.node.hostname, "--set-owner-short", cfg.node.short])
    print(f"radio_init: owner set to {cfg.node.hostname} ({cfg.node.short})")

    # Region only if UNSET (setting it reboots the radio).
    rc, out = _cli(["--get", "lora.region"], timeout=30)
    current = ""
    for line in out.splitlines():
        if "lora.region" in line:
            current = line.split(":", 1)[-1].strip()
            break
    if current in ("", "UNSET", "0"):
        print(f"radio_init: region UNSET — setting to {cfg.meshtastic.region}")
        _cli(["--set", "lora.region", cfg.meshtastic.region])
        # Radio reboots after a region change; wait for it to come back.
        time.sleep(10)
        _wait_for_api(attempts=15)
    else:
        print(f"radio_init: region already {current}")

    # lora.tx_enabled sync every boot: push it only if the radio differs from
    # config, so "receive-only" (tx_enabled=false) survives reboot. Best-effort;
    # a lora.* change can reboot the radio (like a region change), so wait for it
    # to come back if we set it.
    want_tx = cfg.meshtastic.tx_enabled
    rc, out = _cli(["--get", "lora.tx_enabled"], timeout=30)
    current_tx = None
    if rc == 0:
        for line in out.splitlines():
            if "lora.tx_enabled" in line:
                v = line.split(":", 1)[-1].strip().lower()
                current_tx = v in ("true", "1")
                break
    if current_tx is None:
        print("radio_init: could not read lora.tx_enabled — leaving radio alone", file=sys.stderr)
    elif current_tx != want_tx:
        print(f"radio_init: lora.tx_enabled {current_tx} -> {want_tx}")
        _cli(["--set", "lora.tx_enabled", "true" if want_tx else "false"])
        time.sleep(10)
        _wait_for_api(attempts=15)
    else:
        print(f"radio_init: lora.tx_enabled already {current_tx}")

    # Read the radio config into the cache so peers can see our channel and
    # offer a one-click Join without an operator first pressing "Read" in the
    # UI. Same function the Read button runs. Best-effort: never block boot.
    try:
        from .meshtastic_api import _read_config_from_radio
        _read_config_from_radio()
        print("radio_init: radio config cached")
    except Exception as e:  # noqa: BLE001 — best-effort init
        print(f"radio_init: config read failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
