"""Phase 3: apply-time RF reconciles — _reconcile_rf + _reconcile_meshtastic_tx.

Pins the coupled behaviour of the wlan1 mesh toggle (rfkill phy1 + enable/disable
nucleus-mesh/babeld/smcroute) and the Meshtastic receive-only toggle
(lora.tx_enabled pushed only on a delta). All hardware/systemctl/meshtastic calls
are mocked; nothing real runs.
"""

from __future__ import annotations

import copy

import nucleusd.apply as apply
from nucleusd import rf
from nucleusd.schema import NucleusConfig

BASE = {
    "node": {"id": 9},
    "mesh": {"password": "52235223"},
    "ap": {"password": "52235223"},
}


def cfg(**over):
    d = copy.deepcopy(BASE)
    for k, v in over.items():
        d[k] = {**d.get(k, {}), **v}
    return NucleusConfig.model_validate(d)


# --- _reconcile_rf -----------------------------------------------------------

def _capture_rf(monkeypatch, was_blocked=False):
    """Record rfkill block calls, enable/disable calls, and mesh-unit restarts.

    `was_blocked` seeds the pre-state the phy reports via rf.is_blocked, so a test
    can exercise the blocked->unblocked transition (which must restart the mesh).
    """
    rfkill = []
    services = []
    restarts = []
    monkeypatch.setattr(rf, "set_iface_blocked",
                        lambda iface, blocked, **kw: rfkill.append((iface, blocked)) or {"ok": True})
    # _reconcile_rf imports rf as a module attr, so patch there too.
    monkeypatch.setattr(apply.rf, "set_iface_blocked",
                        lambda iface, blocked, **kw: rfkill.append((iface, blocked)) or {"ok": True})
    monkeypatch.setattr(apply.rf, "phy_for_iface", lambda iface, **kw: "phy1")
    monkeypatch.setattr(apply.rf, "is_blocked", lambda phy, **kw: was_blocked)
    monkeypatch.setattr(apply, "_set_service",
                        lambda unit, on: services.append((unit, on)))
    monkeypatch.setattr(apply, "_restart", lambda units: restarts.extend(units))
    return rfkill, services, restarts


def test_reconcile_rf_mesh_enabled_unblocks_and_starts_units(monkeypatch):
    # Steady state: already unblocked -> ensure units on, but no restart.
    rfkill, services, restarts = _capture_rf(monkeypatch, was_blocked=False)
    touched = apply._reconcile_rf(cfg(mesh={"enabled": True}))
    assert (rf.MESH_IFACE, False) in rfkill  # phy1 unblocked
    assert services == [("nucleus-mesh", True), ("babeld", True), ("smcroute", True)]
    assert restarts == []  # no transition -> no disruptive restart
    assert touched == apply.MESH_UNITS


def test_reconcile_rf_mesh_enable_from_blocked_restarts_mesh(monkeypatch):
    # The bug: turning the mesh back ON after an rfkill block must RESTART the
    # mesh units (redo the SAE join + link-local), not just unblock the radio.
    rfkill, services, restarts = _capture_rf(monkeypatch, was_blocked=True)
    apply._reconcile_rf(cfg(mesh={"enabled": True}))
    assert (rf.MESH_IFACE, False) in rfkill
    # Restarted in network-group order so babeld/smcroute rebind after the join.
    assert restarts == ["nucleus-mesh", "babeld", "smcroute"]


def test_reconcile_rf_mesh_disabled_blocks_and_stops_units(monkeypatch):
    rfkill, services, restarts = _capture_rf(monkeypatch, was_blocked=False)
    apply._reconcile_rf(cfg(mesh={"enabled": False}))
    assert (rf.MESH_IFACE, True) in rfkill  # phy1 blocked
    assert services == [("nucleus-mesh", False), ("babeld", False), ("smcroute", False)]
    assert restarts == []


# --- _reconcile_meshtastic_tx ------------------------------------------------

def _capture_mesh_cli(monkeypatch, get_value):
    """Stub _meshtastic_cli: --get returns get_value, record --set calls."""
    sets = []

    def _cli(args, timeout=120):
        if args[:1] == ["--get"]:
            if get_value is None:
                return 1, ""
            return 0, f"lora.tx_enabled: {get_value}\n"
        if args[:2] == ["--set", "lora.tx_enabled"]:
            sets.append(args[2])
        return 0, ""

    monkeypatch.setattr(apply, "_meshtastic_cli", _cli)
    return sets


def test_tx_reconcile_noop_when_meshtastic_disabled(monkeypatch):
    sets = _capture_mesh_cli(monkeypatch, get_value="True")
    changed = apply._reconcile_meshtastic_tx(cfg(meshtastic={"enabled": False}))
    assert changed is False
    assert sets == []  # never even reads the radio


def test_tx_reconcile_noop_when_already_matching(monkeypatch):
    sets = _capture_mesh_cli(monkeypatch, get_value="True")
    changed = apply._reconcile_meshtastic_tx(cfg(meshtastic={"tx_enabled": True}))
    assert changed is False
    assert sets == []


def test_tx_reconcile_turns_tx_off_on_delta(monkeypatch):
    # radio currently True, config wants False -> push "false".
    sets = _capture_mesh_cli(monkeypatch, get_value="True")
    changed = apply._reconcile_meshtastic_tx(cfg(meshtastic={"tx_enabled": False}))
    assert changed is True
    assert sets == ["false"]


def test_tx_reconcile_turns_tx_on_on_delta(monkeypatch):
    sets = _capture_mesh_cli(monkeypatch, get_value="False")
    changed = apply._reconcile_meshtastic_tx(cfg(meshtastic={"tx_enabled": True}))
    assert changed is True
    assert sets == ["true"]


def test_tx_reconcile_leaves_radio_alone_when_unreadable(monkeypatch):
    # --get fails (radio absent/daemon down) -> never --set.
    sets = _capture_mesh_cli(monkeypatch, get_value=None)
    changed = apply._reconcile_meshtastic_tx(cfg(meshtastic={"tx_enabled": False}))
    assert changed is False
    assert sets == []


# --- invariant: mesh units are excludable from the network-group bounce -------

def test_mesh_units_are_subset_of_network_group():
    # apply() drops MESH_UNITS from the group restart when mesh.enabled is False,
    # so a disabled mesh is not restarted back up. That only works if every mesh
    # unit is actually part of the ordered network group.
    assert set(apply.MESH_UNITS) <= set(apply.NET_GROUP_ORDER)
