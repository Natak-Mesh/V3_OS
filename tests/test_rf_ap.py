"""Phase 5: wlan0 AP live toggle — rf.set_ap() + POST /api/v1/rf/ap + UI wiring.

The AP is outside the config pipeline (README §4): a live-only toggle that resets
to ON at boot. Turning it OFF must stop hostapd (NOT disable it) then rfkill phy0;
turning it ON must unblock phy0, start hostapd, and re-enslave wlan0 via
brlan-setup. All rfkill/systemctl calls are mocked; nothing real runs.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from nucleusd import rf
from nucleusd.api import app

client = TestClient(app)


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _capture(monkeypatch):
    """Record every subprocess.run argv rf.set_ap issues; resolve phys + rfkill id."""
    calls = []
    monkeypatch.setattr(rf.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _FakeCompleted())
    monkeypatch.setattr(rf, "phy_for_iface", lambda iface, **kw: "phy0")
    # rfkill addresses phys by numeric index; phy0 -> rfkill index "1" here.
    monkeypatch.setattr(rf, "rfkill_id_for_phy", lambda phy, **kw: "1")
    return calls


def test_set_ap_off_stops_hostapd_then_blocks(monkeypatch):
    calls = _capture(monkeypatch)
    out = rf.set_ap(False)
    assert out == {"ok": True, "on": False}
    # stop (not disable) must come before the rfkill block.
    assert ["systemctl", "stop", "hostapd.service"] in calls
    assert ["rfkill", "block", "1"] in calls
    assert calls.index(["systemctl", "stop", "hostapd.service"]) < \
        calls.index(["rfkill", "block", "1"])
    # never disable — the AP must still start at boot.
    assert not any(c[:2] == ["systemctl", "disable"] for c in calls)


def test_set_ap_on_unblocks_starts_and_reenslaves(monkeypatch):
    calls = _capture(monkeypatch)
    out = rf.set_ap(True)
    assert out == {"ok": True, "on": True}
    assert ["rfkill", "unblock", "1"] in calls
    assert ["systemctl", "start", "hostapd.service"] in calls
    assert ["systemctl", "restart", "brlan-setup.service"] in calls


def test_set_ap_reports_failure_detail(monkeypatch):
    monkeypatch.setattr(rf, "phy_for_iface", lambda iface, **kw: "phy0")
    monkeypatch.setattr(rf, "rfkill_id_for_phy", lambda phy, **kw: "1")

    def _run(cmd, **kw):
        if cmd[:2] == ["systemctl", "stop"]:
            return _FakeCompleted(returncode=1, stderr="unit hostapd not loaded\n")
        return _FakeCompleted()

    monkeypatch.setattr(rf.subprocess, "run", _run)
    out = rf.set_ap(False)
    assert out["ok"] is False
    assert out["detail"] == "unit hostapd not loaded"


# --- API endpoint ------------------------------------------------------------

def test_api_rf_ap_relays_to_set_ap(monkeypatch):
    import nucleusd.rf as rfmod
    seen = {}
    monkeypatch.setattr(rfmod, "set_ap", lambda on: seen.update(on=on) or {"ok": True, "on": on})
    r = client.post("/api/v1/rf/ap", json={"on": False})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "on": False}
    assert seen == {"on": False}


def test_api_rf_ap_requires_on_field():
    r = client.post("/api/v1/rf/ap", json={})
    assert r.status_code == 422  # missing required `on`


# --- web UI wiring -----------------------------------------------------------

def test_radios_page_wired_in_ui():
    app_js = (Path(__file__).resolve().parents[1] / "nucleusd" / "web" / "app.js").read_text()
    assert 'to: "radios"' in app_js, "RADIOS menu entry missing"
    assert "radios: {" in app_js, "radios page definition missing"
    assert "async function rfSetAp" in app_js, "AP toggle handler missing"
    assert "/api/v1/rf/ap" in app_js, "AP handler no longer calls the API"
    assert "async function rfSetMesh" in app_js, "mesh toggle handler missing"
    assert "async function rfSetMeshtasticTx" in app_js, "Meshtastic TX handler missing"
    # Lockout guard: confirms must name the wired fallbacks.
    assert "USB-C" in app_js and "eth0" in app_js, "lockout confirm fallbacks missing"
