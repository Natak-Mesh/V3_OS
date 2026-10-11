"""Template rendering tests — assert generated files contain correct values."""

from nucleusd.apply import render_all
from nucleusd.schema import NucleusConfig

CFG = NucleusConfig.model_validate({
    "node": {"id": 9},
    "mesh": {"password": "52235223"},
    "ap": {"password": "52235223"},
})


def rendered():
    return render_all(CFG)


def test_all_targets_render():
    r = rendered()
    assert len(r) == 10
    assert all(v.strip() for v in r.values())


def test_babeld_has_subnets():
    b = rendered()["/etc/babeld.conf"]
    assert "redistribute ip 10.20.1.0/24 allow" in b
    assert "redistribute ip 10.20.9.0/24 allow" in b   # br-lan = 10.20.<id>.0
    assert "local-port 33123" in b


def test_smcroute_no_echo():
    s = rendered()["/etc/smcroute.conf"]
    # No-echo: ingress routes go only to the OTHER interface.
    assert "mroute from wlan1 group 239.2.3.1 to br-lan" in s
    assert "to wlan1 br-lan" not in s  # would be the amplification bug


def test_wpa_mesh_fwding_off():
    w = rendered()["/etc/wpa_supplicant/wpa_supplicant-mesh.conf"]
    assert "mesh_fwding=0" in w
    assert "key_mgmt=SAE" in w
    assert "frequency=2422" in w


def test_meshup_sets_params():
    m = rendered()["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    assert "iw dev wlan1 set mesh_param mesh_fwding=0" in m
    assert "mesh_ttl=8" in m
    assert "set rts 500" in m
    assert "masquerade" in m  # eth0 wan NAT


def test_meshup_firewall_rules():
    m = rendered()["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    # Default-deny inbound, but ssh over ethernet must survive.
    assert "ufw --force default deny incoming" in m
    assert "ufw allow in on eth0 to any port 22 proto tcp" in m
    # Internal interfaces trusted.
    assert "ufw allow in on wlan1" in m
    assert "ufw allow in on br-lan" in m
    # WAN mode (default eth0 mode) routes clients out to the internet.
    assert "ufw route allow in on br-lan out on eth0" in m
    assert "ufw --force enable" in m


def test_meshup_firewall_reset_before_allow():
    # config.yaml is the single source of truth: the ruleset is rebuilt every
    # run, so a reset must precede the first `ufw allow` (no stale rules linger).
    m = rendered()["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    assert "ufw --force reset" in m
    assert m.index("ufw --force reset") < m.index("ufw allow")
    # ssh-on-eth0 is added first, right after the reset, so admin access over
    # ethernet is never left dangling.
    assert m.index("ufw allow in on eth0 to any port 22 proto tcp") < \
        m.index("ufw allow in on wlan1")
    # Reset leaves timestamped rule-file backups; they must be cleaned up.
    assert "rm -f /etc/ufw/*.rules.[0-9]*_[0-9]*" in m


def test_meshup_no_removed_subsystems():
    # Drone build: no Tailscale trust, no TAK/OTS/MediaMTX eth0 ports.
    m = rendered()["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    assert "tailscale" not in m
    for word in ("takserver", "opentakserver", "mediamtx", "dpkg-query"):
        assert word not in m


def test_meshup_firewall_disabled():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "firewall": {"enabled": False},
    })
    m = render_all(cfg)["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    # Firewall off: ufw is disabled and no rules are rendered at all.
    assert "ufw --force disable" in m
    assert "ufw allow" not in m
    assert "ufw route" not in m
    assert "ufw --force enable" not in m
    assert "ufw --force reset" not in m


def test_meshup_no_wan_route_in_lan_mode():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "eth0": {"mode": "lan", "static_ip": "10.10.9.1"},
    })
    m = render_all(cfg)["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    # No internet egress routing when eth0 is a LAN port, but ssh + mesh<->lan
    # forwarding still apply.
    assert "ufw route allow in on br-lan out on eth0" not in m
    assert "ufw allow in on eth0 to any port 22 proto tcp" in m
    assert "ufw route allow in on br-lan out on wlan1" in m


def test_hostapd_no_bridge_directive():
    h = rendered()["/etc/hostapd/hostapd.conf"]
    # brlan-setup.service enslaves wlan0 into br-lan; a hostapd bridge= line
    # would fight networkd over it (see hostapd.conf.j2).
    assert "\nbridge=" not in h
    assert "ssid=0009-nucleus-ap" in h


def test_brlan_setup_restarts_with_networkd_and_hostapd():
    from nucleusd.apply import TARGETS
    for t in TARGETS:
        if "systemd-networkd" in t.units or "hostapd" in t.units:
            assert "brlan-setup" in t.units, t.template


def test_eth0_lan_mode():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "eth0": {"mode": "lan", "static_ip": "10.10.9.1"},
    })
    e = render_all(cfg)["/etc/systemd/network/40-eth0.network"]
    assert "Address=10.10.9.1/24" in e
    assert "DHCPServer=yes" in e
