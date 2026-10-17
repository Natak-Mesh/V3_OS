"""status.radios() — per-radio on/off state for the Radios page (Phase 4/5).

Combines the config source-of-truth (mesh.enabled, meshtastic.tx_enabled) with
live rfkill + unit state. All hardware/systemctl/config calls are mocked.
"""

from nucleusd import status
from nucleusd import rf


class _Mesh:
    enabled = False


class _Meshtastic:
    tx_enabled = False


class _Halow:
    enabled = False
    channel = 27
    bandwidth_mhz = 1


class _Cfg:
    mesh = _Mesh()
    meshtastic = _Meshtastic()
    halow = _Halow()
    halow_freq_mhz = 915.5


def _wire(monkeypatch, *, cfg, phy_blocked, hostapd, halow_present=False):
    """Stub config.load, rf phy/rfkill, unit_states and iface_addrs for radios()."""
    import nucleusd.config as cfgio
    monkeypatch.setattr(cfgio, "load", lambda: cfg)
    monkeypatch.setattr(rf, "phy_for_iface",
                        lambda iface, **kw: {"wlan1": "phy1", "wlan0": "phy0"}.get(iface))
    monkeypatch.setattr(rf, "is_blocked", lambda phy: phy_blocked.get(phy))
    monkeypatch.setattr(status, "unit_states",
                        lambda units=(): {"meshtasticd": "active", "hostapd": hostapd,
                                          "nucleus-halow": "active" if halow_present else "inactive"})
    state = "up" if halow_present else "absent"
    monkeypatch.setattr(status, "iface_addrs",
                        lambda names=(): {"halow0": {"state": state, "addrs": []}})


def test_radios_reports_config_and_live_state(monkeypatch):
    # mesh disabled + phy1 blocked; tx receive-only; AP up and unblocked.
    _wire(monkeypatch, cfg=_Cfg(),
          phy_blocked={"phy1": True, "phy0": False}, hostapd="active")
    r = status.radios()
    assert r["mesh"]["on"] is False
    assert r["mesh"]["rf_blocked"] is True
    assert r["mesh"]["persistent"] is True
    assert r["meshtastic"]["on"] is False
    assert r["meshtastic"]["receive_only"] is True
    assert r["ap"]["on"] is True          # hostapd active + phy0 not blocked
    assert r["ap"]["persistent"] is False


def test_radios_ap_off_when_blocked(monkeypatch):
    _wire(monkeypatch, cfg=_Cfg(),
          phy_blocked={"phy1": False, "phy0": True}, hostapd="active")
    r = status.radios()
    assert r["ap"]["on"] is False  # soft-blocked even though hostapd is active


def test_radios_degrades_when_config_unavailable(monkeypatch):
    import nucleusd.config as cfgio

    def _boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(cfgio, "load", _boom)
    monkeypatch.setattr(rf, "phy_for_iface", lambda iface, **kw: None)
    monkeypatch.setattr(status, "unit_states",
                        lambda units=(): {"meshtasticd": "inactive", "hostapd": "inactive"})
    monkeypatch.setattr(status, "iface_addrs",
                        lambda names=(): {"halow0": {"state": "absent", "addrs": []}})
    r = status.radios()
    assert r["mesh"]["on"] is None            # config unreadable -> unknown
    assert r["meshtastic"]["on"] is None
    assert r["mesh"]["rf_blocked"] is None     # no phy -> unknown
    assert r["ap"]["on"] is False              # hostapd inactive
    assert r["halow"]["on"] is None            # config unreadable -> unknown