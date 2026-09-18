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
    assert "redistribute ip 10.20.12.0/24 allow" in b
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


def test_hostapd_bridges_brlan():
    h = rendered()["/etc/hostapd/hostapd.conf"]
    assert "bridge=br-lan" in h
    assert "ssid=0009-nucleus-ap" in h


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
