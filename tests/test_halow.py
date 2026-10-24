"""Wi-Fi HaLow (802.11ah) integration — schema, render, apply.

Covers docs/halow.md §10.8. Off-box: no radio, no root, all subprocess mocked.
"""

import copy

import pytest
from pydantic import ValidationError

import nucleusd.apply as apply
from nucleusd.schema import (
    HALOW_CHANNELS, HALOW_IFACE, HALOW_OP_CLASS, NucleusConfig,
)

BASE = {
    "node": {"id": 42},
    "mesh": {"password": "52235223", "ssid": "natak_mesh"},
    "ap": {"password": "52235223"},
}


def cfg(**over):
    d = copy.deepcopy(BASE)
    for k, v in over.items():
        d[k] = {**d.get(k, {}), **v}
    return NucleusConfig.model_validate(d)


# ── schema: valid + invalid channel/bandwidth/prim combos ──────────────────

def test_halow_defaults():
    c = cfg()
    assert c.halow.enabled is True
    assert c.halow.bandwidth_mhz == 1
    assert c.halow.channel == 27
    assert c.halow.prim_1mhz_index == 0


@pytest.mark.parametrize("bw,ch", [(1, 27), (4, 8), (8, 12), (2, 6)])
def test_halow_valid_channel_bandwidth(bw, ch):
    c = cfg(halow={"bandwidth_mhz": bw, "channel": ch})
    assert c.halow.channel == ch
    assert ch in HALOW_CHANNELS[bw]


def test_halow_invalid_channel_for_bandwidth():
    # ch 27 is a 1 MHz channel; invalid at 2 MHz.
    with pytest.raises(ValidationError):
        cfg(halow={"bandwidth_mhz": 2, "channel": 27})


def test_halow_prim_index_out_of_range_for_bandwidth():
    # 1 MHz allows only prim index 0.
    with pytest.raises(ValidationError):
        cfg(halow={"bandwidth_mhz": 1, "channel": 27, "prim_1mhz_index": 1})
    # 4 MHz allows 0-3; 4 is out of range.
    with pytest.raises(ValidationError):
        cfg(halow={"bandwidth_mhz": 4, "channel": 8, "prim_1mhz_index": 4})
    # 4 MHz with a valid index passes.
    assert cfg(halow={"bandwidth_mhz": 4, "channel": 8,
                      "prim_1mhz_index": 3}).halow.prim_1mhz_index == 3


def test_halow_non_us_country_rejected_when_enabled():
    with pytest.raises(ValidationError):
        cfg(mesh={"country": "GB"}, halow={"enabled": True})
    # Disabled HaLow lifts the gate (non-US is fine if HaLow is off).
    assert cfg(mesh={"country": "GB"}, halow={"enabled": False}).halow.enabled is False


# ── schema: derived values ─────────────────────────────────────────────────

def test_halow_derived_values():
    c = cfg()
    assert c.halow_ip == "10.30.1.42"
    assert c.halow_ssid == "natak_mesh_halow"
    assert c.halow_op_class == HALOW_OP_CLASS[1] == 68
    assert c.halow_freq_mhz == 915.5           # 902 + 0.5 * 27
    assert c.halow_ipv6_ll.startswith("fe80::")
    assert c.mesh_ifaces == ["wlan1", HALOW_IFACE]


def test_mesh_ifaces_drops_halow_when_disabled():
    assert cfg(halow={"enabled": False}).mesh_ifaces == ["wlan1"]


def test_halow_op_class_per_bandwidth():
    assert cfg(halow={"bandwidth_mhz": 8, "channel": 12}).halow_op_class == 71
    assert cfg(halow={"bandwidth_mhz": 4, "channel": 8}).halow_op_class == 70


# ── render: HaLow-enabled stanzas present ───────────────────────────────────

def _render(c):
    return apply.render_all(c)


def test_render_networkd_and_supplicant_and_modprobe():
    r = _render(cfg())
    net = r["/etc/systemd/network/12-halow0.network"]
    assert "Name=halow0" in net
    assert "Address=10.30.1.42/24" in net

    sup = r["/etc/wpa_supplicant/wpa_supplicant-halow.conf"]
    assert 'ssid="natak_mesh_halow"' in sup
    assert "channel=27" in sup
    assert "op_class=68" in sup
    assert "s1g_prim_1mhz_chan_index=0" in sup
    assert "mode=5" in sup

    assert 'options morse country="US"' in r["/etc/modprobe.d/nucleus-morse.conf"]


def test_render_babeld_and_smcroute_halow():
    r = _render(cfg())
    babel = r["/etc/babeld.conf"]
    assert "interface halow0 type wireless" in babel
    assert "redistribute ip 10.30.1.0/24 allow" in babel

    smc = r["/etc/smcroute.conf"]
    # br-lan <-> halow0 bridged...
    assert "mroute from halow0 group 239.2.3.1 to br-lan" in smc
    assert "mroute from br-lan group 239.2.3.1 to halow0" in smc
    # ...but NEVER wlan1 <-> halow0.
    assert "from wlan1 group 239.2.3.1 to halow0" not in smc
    assert "from halow0 group 239.2.3.1 to wlan1" not in smc


def test_render_ufw_and_reticulum_halow():
    r = _render(cfg())
    # UFW now lives in nucleus-firewall.sh (decoupled from the wlan1 mesh unit).
    fw = r["/opt/nucleus/bin/nucleus-firewall.sh"]
    assert "ufw allow in on halow0" in fw
    assert "ufw route allow in on br-lan out on halow0" in fw
    assert "ufw route allow in on halow0 out on br-lan" in fw

    assert "devices = wlan1, halow0" in r["/home/natak/.reticulum/config"]


def test_render_halow_stanzas_vanish_when_disabled():
    r = _render(cfg(halow={"enabled": False}))
    assert "interface halow0" not in r["/etc/babeld.conf"]
    assert "redistribute ip 10.30.1.0/24" not in r["/etc/babeld.conf"]
    assert "halow0" not in r["/etc/smcroute.conf"]
    assert "halow0" not in r["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    reti = r["/home/natak/.reticulum/config"]
    # halow0 drops out of the AutoInterface; wlan1 + eth0 remain.
    assert "devices = wlan1, eth0\n" in reti
    assert "halow0" not in reti


# ── apply: _reconcile_halow enable/disable + restart-on-change ──────────────

def _capture(monkeypatch):
    services, restarts = [], []
    monkeypatch.setattr(apply, "_set_service",
                        lambda unit, on: services.append((unit, on)))
    monkeypatch.setattr(apply, "_restart", lambda units: restarts.extend(units))
    return services, restarts


def test_reconcile_halow_enables_and_restarts_on_conf_change(monkeypatch):
    services, restarts = _capture(monkeypatch)
    touched = apply._reconcile_halow(
        cfg(halow={"enabled": True}),
        changed=[str(apply.HALOW_SUPPLICANT_CONF)],
    )
    assert ("nucleus-halow", True) in services
    assert restarts == ["nucleus-halow"]
    assert touched == ["nucleus-halow"]


def test_reconcile_halow_enabled_no_change_no_restart(monkeypatch):
    services, restarts = _capture(monkeypatch)
    apply._reconcile_halow(cfg(halow={"enabled": True}), changed=[])
    assert ("nucleus-halow", True) in services
    assert restarts == []


def test_reconcile_halow_disabled_stops_no_restart(monkeypatch):
    services, restarts = _capture(monkeypatch)
    apply._reconcile_halow(
        cfg(halow={"enabled": False}),
        changed=[str(apply.HALOW_UP_SCRIPT)],  # change ignored when disabled
    )
    assert ("nucleus-halow", False) in services
    assert restarts == []


def test_halow_unit_in_network_group():
    assert apply.HALOW_UNIT in apply.NET_GROUP_ORDER
    assert "halow0" in apply.NETWORKD_LINKS


# ── config normalize: a config without halow: gains it with defaults ────────

def test_normalize_adds_halow_defaults(tmp_path):
    from nucleusd import config as cfgio
    p = tmp_path / "config.yaml"
    p.write_text(
        "node:\n  id: 7\n"
        "mesh:\n  subnet_prefix: 10.20.1\n  password: pw123456\n"
        "ap:\n  password: ap123456\n"
    )
    assert cfgio.normalize(p) is True
    loaded = cfgio.load(p)
    assert loaded.halow.enabled is True
    assert loaded.halow.channel == 27
    assert loaded.halow.bandwidth_mhz == 1
