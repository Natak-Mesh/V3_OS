"""Network-group restart behaviour in apply() — fixes C and D.

C: when any member of the network group changes, apply() must bounce the WHOLE
   group in a fixed order (not just the changed unit), and reconfigure networkd
   links rather than only reloading. Restarting one unit in isolation leaves the
   others bound to stale interfaces/multicast memberships (half-broken-until-
   reboot).
D: the static brlan-setup unit must wait for wlan0 AP mode instead of a fixed
   sleep, and own the bridge-enslave step.
"""

from pathlib import Path

import nucleusd.apply as apply
from nucleusd.apply import NET_GROUP_ORDER, NETWORKD_LINKS, _restart


def _record_restart(monkeypatch):
    """Capture every subprocess.run argv issued by _restart()."""
    calls = []
    monkeypatch.setattr(apply.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or _Done())
    return calls


class _Done:
    returncode = 0


def test_networkd_links_are_reconfigured_not_reloaded(monkeypatch):
    # Fix C: existing links need `networkctl reconfigure <links>`; `reload` only
    # re-reads files and leaves live links on their old config.
    calls = _record_restart(monkeypatch)
    _restart(["systemd-networkd"])
    assert calls == [["networkctl", "reconfigure", *NETWORKD_LINKS]]


def test_restart_order_is_fixed(monkeypatch):
    # hostapd must precede brlan-setup (AP up before enslave); routing daemons
    # (babeld, smcroute) come after the interfaces exist.
    calls = _record_restart(monkeypatch)
    _restart(NET_GROUP_ORDER)
    restarted = [c[2].removesuffix(".service") for c in calls if c[0] == "systemctl"]
    assert restarted == ["nucleus-mesh", "nucleus-halow", "hostapd", "brlan-setup", "babeld", "smcroute"]
    assert restarted.index("hostapd") < restarted.index("brlan-setup")
    # HaLow join is ordered after the primary mesh, before the routing daemons.
    assert restarted.index("nucleus-mesh") < restarted.index("nucleus-halow")
    assert restarted.index("nucleus-halow") < restarted.index("babeld")


def test_group_order_has_no_duplicates_and_expected_members():
    assert NET_GROUP_ORDER == [
        "systemd-networkd", "nucleus-mesh", "nucleus-halow", "hostapd",
        "brlan-setup", "babeld", "smcroute",
    ]


def test_brlan_setup_unit_waits_for_ap_mode():
    # Fix D: no blind `sleep 2` race; wait for wlan0 `type AP`, then enslave.
    unit = Path(__file__).resolve().parents[1] / "system/systemd/brlan-setup.service"
    text = unit.read_text()
    assert "sleep 2\n" not in text
    assert "type AP" in text
    assert "ip link set wlan0 master br-lan" in text
