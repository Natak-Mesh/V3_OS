"""Per-radio RF control via rfkill (the ONLY module that touches rfkill/sysfs).

Phase 1 of docs/rf-control.md. Keeps all rfkill/`/sys` access isolated here so
schema.py stays pure (README §2) and apply/status/boot share one implementation.

A wireless interface is soft-blocked by its *phy* (phyN), not by a fixed rfkill
index: rfkill indices and USB device paths are not stable across reboots or
re-plugs, but the phy a netdev belongs to is resolvable from sysfs. We therefore
resolve `<iface> -> phyN` via /sys/class/net/<iface>/phy80211 (a symlink whose
basename is the phy name) and hand that phy name straight to `rfkill`.

nucleusd runs as root, so rfkill is invoked directly (no sudo).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# sysfs roots; overridable in tests with fixture trees.
NET_SYSFS = Path("/sys/class/net")
RFKILL_SYSFS = Path("/sys/class/rfkill")


def phy_for_iface(iface: str, sysfs: Path | None = None) -> str | None:
    """Return the phy name (e.g. 'phy1') backing a wireless interface, or None.

    Reads /sys/class/net/<iface>/phy80211, a symlink into the device's phy; its
    basename is the phy name. Returns None for a missing interface or a
    non-wireless one (no phy80211 link).
    """
    root = sysfs or NET_SYSFS
    link = root / iface / "phy80211"
    try:
        # Resolve the symlink without requiring the target to exist on disk
        # (test fixtures may point at a synthetic path); the basename is enough.
        target = link.readlink() if link.is_symlink() else None
    except OSError:
        return None
    if target is None:
        return None
    name = target.name
    return name or None


def rfkill_id_for_phy(phy: str, sysfs: Path | None = None) -> str | None:
    """Return the rfkill *index* (as a string, e.g. "2") for a phy name, or None.

    util-linux `rfkill` only accepts a numeric index or a type as its identifier —
    NOT the phy name ("rfkill: invalid identifier: phy1"). The index for a phy is
    exposed at /sys/class/rfkill/rfkillN/name. rfkill indices aren't stable across
    reboots/re-plugs, so we resolve the current one each call rather than caching.
    """
    root = sysfs or RFKILL_SYSFS
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            if (entry / "name").read_text().strip() == phy:
                # Directory is named "rfkillN"; the index is the trailing part.
                return entry.name.removeprefix("rfkill") or None
        except OSError:
            continue
    return None


def _rfkill(action: str, phy: str) -> dict:
    """Run `rfkill <action> <index>` for a phy; never raise, report as a dict.

    Resolves the phy's current rfkill index first (rfkill rejects phy names).
    """
    rid = rfkill_id_for_phy(phy)
    if rid is None:
        return {"ok": False, "detail": f"no rfkill index for {phy}"}
    r = subprocess.run(
        ["rfkill", action, rid],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return {"ok": False, "detail": (r.stderr or f"rfkill {action} {rid} failed").strip()}
    return {"ok": True}


def block(phy: str) -> dict:
    """Soft-block (RF off) the given phy."""
    return _rfkill("block", phy)


def unblock(phy: str) -> dict:
    """Soft-unblock (RF on) the given phy."""
    return _rfkill("unblock", phy)


def is_blocked(phy: str, sysfs: Path | None = None) -> bool | None:
    """Return True if `phy` is soft-blocked, False if not, None if unknown.

    Reads /sys/class/rfkill/rfkillN/soft (1 = blocked) directly instead of parsing
    `rfkill list`, which avoids the identifier problem and is faster. Returns None
    when the phy has no rfkill entry, so callers can tell "unknown" apart from a
    definite on/off state.
    """
    root = sysfs or RFKILL_SYSFS
    rid = rfkill_id_for_phy(phy, sysfs=root)
    if rid is None:
        return None
    try:
        return (root / f"rfkill{rid}" / "soft").read_text().strip() == "1"
    except OSError:
        return None


def set_iface_blocked(iface: str, blocked: bool, sysfs: Path | None = None) -> dict:
    """Block/unblock the phy backing an interface. Convenience for apply/boot.

    Resolves the phy from the interface, then applies the desired state. Returns
    {"ok": False, "detail": ...} if the phy can't be resolved.
    """
    phy = phy_for_iface(iface, sysfs=sysfs)
    if phy is None:
        return {"ok": False, "detail": f"no phy for interface {iface}"}
    return block(phy) if blocked else unblock(phy)


# Interface each radio lives on (README addressing: wlan1 mesh, wlan0 AP).
MESH_IFACE = "wlan1"
AP_IFACE = "wlan0"


def enforce_boot_state() -> dict:
    """Apply the persistent RF state at boot. Best-effort; never raises.

    Run by nucleus-rf.service (oneshot) After=systemd-rfkill.service and before
    nucleus-mesh/hostapd. Two jobs:

      * wlan1 mesh — block/unblock phy1 to match `mesh.enabled` (the config is
        the source of truth; this makes the state survive reboot).
      * wlan0 AP  — ALWAYS unblock phy0. The AP is "outside the pipeline"
        (README §4): it has no config flag and must reset to ON every boot.
        systemd-rfkill restores whatever block state was saved, so a previously
        soft-blocked AP would otherwise stay blocked — we explicitly undo that.

    Returns a dict of per-iface outcomes for logging/tests.
    """
    from . import config as cfgio

    results: dict[str, dict] = {}

    # wlan1 mesh: enforce config. If config can't load, leave the mesh untouched
    # (don't guess), but still reset the AP below.
    try:
        mesh_enabled = cfgio.load().mesh.enabled
    except Exception as e:  # noqa: BLE001 — best-effort boot
        results["mesh"] = {"ok": False, "detail": f"config load failed: {e}"}
    else:
        results["mesh"] = set_iface_blocked(MESH_IFACE, blocked=not mesh_enabled)

    # wlan0 AP: always on at boot.
    results["ap"] = set_iface_blocked(AP_IFACE, blocked=False)
    return results


def _systemctl(action: str, unit: str) -> dict:
    """Run `systemctl <action> <unit>`; never raise, report as a dict."""
    r = subprocess.run(
        ["systemctl", action, unit],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return {"ok": False, "detail": (r.stderr or f"systemctl {action} {unit} failed").strip()}
    return {"ok": True}


def set_ap(on: bool) -> dict:
    """Turn the wlan0 AP on/off live. Outside the config pipeline (README §4).

    The AP has no config flag and must reset to ON at boot (nucleus-rf.service
    unblocks phy0 every boot), so this is a live-only toggle:

      * OFF: stop hostapd (NOT disable — must still start at boot), then block
        phy0.
      * ON:  unblock phy0, start hostapd, then re-run brlan-setup so wlan0 is
        re-enslaved into br-lan (restarting hostapd drops it out of the bridge).

    stop/start (not disable/enable) deliberately leaves the boot-time enabled
    state untouched. Returns {"ok": bool, "on": bool, "detail"?: str}.
    """
    if on:
        rfk = unblock(phy_for_iface(AP_IFACE) or "")
        svc = _systemctl("start", "hostapd.service")
        # Re-enslave wlan0 into br-lan once hostapd owns it again.
        brlan = _systemctl("restart", "brlan-setup.service")
        ok = rfk["ok"] and svc["ok"]
        detail = svc.get("detail") or rfk.get("detail") or brlan.get("detail")
    else:
        # Stop the AP first, then kill RF, so clients drop cleanly.
        svc = _systemctl("stop", "hostapd.service")
        rfk = set_iface_blocked(AP_IFACE, blocked=True)
        ok = svc["ok"] and rfk["ok"]
        detail = svc.get("detail") or rfk.get("detail")
    out = {"ok": ok, "on": on}
    if detail and not ok:
        out["detail"] = detail
    return out


def main() -> None:
    """Entry point for nucleus-rf.service (`python -m nucleusd.rf`)."""
    import sys

    for radio, outcome in enforce_boot_state().items():
        if outcome.get("ok"):
            print(f"rf: {radio} ok")
        else:
            print(f"rf: {radio}: {outcome.get('detail')}", file=sys.stderr)


if __name__ == "__main__":
    main()
