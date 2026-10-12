"""nucleusd.rf — per-radio RF control. Run off-box: `pytest` from repo root.

rf.py is the only module allowed to touch rfkill/sysfs, so these tests pin:
  * phy resolution from a real sysfs-style fixture tree (symlink basename),
  * rfkill index resolution from /sys/class/rfkill/rfkillN/name,
  * block/unblock issuing `rfkill <action> <index>` (NOT the phy name, which
    util-linux rfkill rejects), with subprocess mocked,
  * is_blocked() reading /sys/class/rfkill/rfkillN/soft directly,
  * set_iface_blocked() end-to-end wiring + the no-phy failure path.
No real rfkill ever runs; subprocess.run is monkeypatched.
"""

from __future__ import annotations

from nucleusd import rf


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_iface(sysfs, iface, phy):
    """Build /sys/class/net/<iface>/phy80211 -> ../../ieee80211/<phy> like the kernel."""
    netdir = sysfs / iface
    netdir.mkdir(parents=True)
    phydir = sysfs.parent / "ieee80211" / phy
    phydir.mkdir(parents=True)
    (netdir / "phy80211").symlink_to(phydir)


def _make_rfkill(tmp_path, monkeypatch, mapping):
    """Build /sys/class/rfkill/rfkill<N>/{name,soft} and point rf.RFKILL_SYSFS at it.

    `mapping` is {index: (name, soft)} e.g. {2: ("phy1", 0)}. Returns the root.
    """
    root = tmp_path / "rfkill"
    root.mkdir(parents=True, exist_ok=True)
    for idx, (name, soft) in mapping.items():
        d = root / f"rfkill{idx}"
        d.mkdir()
        (d / "name").write_text(name + "\n")
        (d / "soft").write_text(f"{soft}\n")
    monkeypatch.setattr(rf, "RFKILL_SYSFS", root)
    return root


# --- phy resolution ----------------------------------------------------------

def test_phy_for_iface_resolves_symlink_basename(tmp_path):
    sysfs = tmp_path / "class" / "net"
    _make_iface(sysfs, "wlan1", "phy1")
    assert rf.phy_for_iface("wlan1", sysfs=sysfs) == "phy1"


def test_phy_for_iface_missing_iface_returns_none(tmp_path):
    sysfs = tmp_path / "class" / "net"
    sysfs.mkdir(parents=True)
    assert rf.phy_for_iface("wlan9", sysfs=sysfs) is None


def test_phy_for_iface_non_wireless_returns_none(tmp_path):
    sysfs = tmp_path / "class" / "net"
    (sysfs / "eth0").mkdir(parents=True)  # no phy80211 link
    assert rf.phy_for_iface("eth0", sysfs=sysfs) is None


# --- block / unblock ---------------------------------------------------------

# --- rfkill index resolution -------------------------------------------------

def test_rfkill_id_for_phy_reads_name(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {0: ("hci0", 1), 1: ("phy0", 0), 2: ("phy1", 0)})
    assert rf.rfkill_id_for_phy("phy1") == "2"
    assert rf.rfkill_id_for_phy("phy0") == "1"


def test_rfkill_id_for_phy_missing_returns_none(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {0: ("hci0", 1)})
    assert rf.rfkill_id_for_phy("phy1") is None


# --- block / unblock (issue the numeric index, not the phy name) -------------

def test_block_issues_index_command(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {2: ("phy1", 0)})
    calls = []
    monkeypatch.setattr(rf.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _FakeCompleted())
    assert rf.block("phy1") == {"ok": True}
    assert calls == [["rfkill", "block", "2"]]


def test_unblock_issues_index_command(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {1: ("phy0", 1)})
    calls = []
    monkeypatch.setattr(rf.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _FakeCompleted())
    assert rf.unblock("phy0") == {"ok": True}
    assert calls == [["rfkill", "unblock", "1"]]


def test_block_unknown_phy_fails_without_running_rfkill(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {0: ("hci0", 1)})  # no phy7

    def _boom(*a, **kw):
        raise AssertionError("rfkill must not run without an index")

    monkeypatch.setattr(rf.subprocess, "run", _boom)
    r = rf.block("phy7")
    assert r["ok"] is False
    assert "phy7" in r["detail"]


def test_block_failure_reports_stderr(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {2: ("phy1", 0)})
    monkeypatch.setattr(rf.subprocess, "run",
                        lambda cmd, **kw: _FakeCompleted(returncode=1, stderr="no such device\n"))
    r = rf.block("phy1")
    assert r == {"ok": False, "detail": "no such device"}


# --- is_blocked (reads rfkillN/soft directly) --------------------------------

def test_is_blocked_true(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {2: ("phy1", 1)})
    assert rf.is_blocked("phy1") is True


def test_is_blocked_false(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {1: ("phy0", 0)})
    assert rf.is_blocked("phy0") is False


def test_is_blocked_unknown_when_phy_absent(tmp_path, monkeypatch):
    _make_rfkill(tmp_path, monkeypatch, {0: ("hci0", 1)})
    assert rf.is_blocked("phy9") is None


# --- set_iface_blocked -------------------------------------------------------

def test_set_iface_blocked_resolves_phy_and_blocks(tmp_path, monkeypatch):
    sysfs = tmp_path / "class" / "net"
    _make_iface(sysfs, "wlan1", "phy1")
    _make_rfkill(tmp_path, monkeypatch, {2: ("phy1", 0)})
    calls = []
    monkeypatch.setattr(rf.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _FakeCompleted())
    assert rf.set_iface_blocked("wlan1", True, sysfs=sysfs) == {"ok": True}
    assert calls == [["rfkill", "block", "2"]]


def test_set_iface_blocked_unblocks(tmp_path, monkeypatch):
    sysfs = tmp_path / "class" / "net"
    _make_iface(sysfs, "wlan0", "phy0")
    _make_rfkill(tmp_path, monkeypatch, {1: ("phy0", 1)})
    calls = []
    monkeypatch.setattr(rf.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _FakeCompleted())
    assert rf.set_iface_blocked("wlan0", False, sysfs=sysfs) == {"ok": True}
    assert calls == [["rfkill", "unblock", "1"]]


def test_set_iface_blocked_no_phy_fails_without_running_rfkill(tmp_path, monkeypatch):
    sysfs = tmp_path / "class" / "net"
    sysfs.mkdir(parents=True)

    def _boom(*a, **kw):
        raise AssertionError("rfkill must not run when the phy is unresolved")

    monkeypatch.setattr(rf.subprocess, "run", _boom)
    r = rf.set_iface_blocked("wlan1", True, sysfs=sysfs)
    assert r["ok"] is False
    assert "wlan1" in r["detail"]


# --- enforce_boot_state (nucleus-rf.service) ---------------------------------

class _FakeCfg:
    """Stand-in for a loaded NucleusConfig exposing only mesh.enabled."""
    def __init__(self, mesh_enabled):
        self.mesh = type("M", (), {"enabled": mesh_enabled})()


# phy -> rfkill index used across the boot-state tests.
_PHY_RID = {"phy1": "2", "phy0": "1"}


def _capture_rfkill(monkeypatch):
    """Record (action, rfkill_index) calls; stub phy->index resolution."""
    calls = []
    monkeypatch.setattr(rf, "rfkill_id_for_phy", lambda phy, **kw: _PHY_RID.get(phy))
    monkeypatch.setattr(rf.subprocess, "run",
                        lambda cmd, **kw: calls.append(tuple(cmd[1:])) or _FakeCompleted())
    return calls


def test_boot_mesh_on_unblocks_both_phys(monkeypatch):
    # mesh.enabled=True -> wlan1 unblocked; AP always unblocked.
    monkeypatch.setattr(rf, "phy_for_iface",
                        lambda iface, sysfs=None: {"wlan1": "phy1", "wlan0": "phy0"}[iface])
    from nucleusd import config as cfgio
    monkeypatch.setattr(cfgio, "load", lambda: _FakeCfg(True))
    calls = _capture_rfkill(monkeypatch)
    res = rf.enforce_boot_state()
    assert res["mesh"]["ok"] and res["ap"]["ok"]
    assert ("unblock", "2") in calls  # phy1
    assert ("unblock", "1") in calls  # phy0
    assert not any(a == "block" for a, _ in calls)


def test_boot_mesh_off_blocks_wlan1_but_ap_stays_on(monkeypatch):
    # mesh.enabled=False -> wlan1 blocked; AP STILL unblocked (reset-on-boot).
    monkeypatch.setattr(rf, "phy_for_iface",
                        lambda iface, sysfs=None: {"wlan1": "phy1", "wlan0": "phy0"}[iface])
    from nucleusd import config as cfgio
    monkeypatch.setattr(cfgio, "load", lambda: _FakeCfg(False))
    calls = _capture_rfkill(monkeypatch)
    rf.enforce_boot_state()
    assert ("block", "2") in calls    # phy1 blocked
    assert ("unblock", "1") in calls  # phy0 unblocked


def test_boot_config_load_failure_still_resets_ap(monkeypatch):
    # If config can't load, leave the mesh alone but always reset the AP on.
    monkeypatch.setattr(rf, "phy_for_iface",
                        lambda iface, sysfs=None: {"wlan1": "phy1", "wlan0": "phy0"}[iface])
    from nucleusd import config as cfgio

    def _raise():
        raise RuntimeError("no config")

    monkeypatch.setattr(cfgio, "load", _raise)
    calls = _capture_rfkill(monkeypatch)
    res = rf.enforce_boot_state()
    assert res["mesh"]["ok"] is False
    assert ("unblock", "1") in calls          # phy0 (AP) still reset on
    assert not any(rid == "2" for _, rid in calls)  # phy1 (mesh) untouched
