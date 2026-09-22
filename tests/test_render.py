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
    assert len(r) == 14
    assert all(v.strip() for v in r.values())


def test_reticulum_config_defaults():
    r = rendered()["/home/natak/.reticulum/config"]
    assert "enable_transport = Yes" in r
    assert "loglevel = 4" in r
    # AutoInterface on the wlan1 mesh.
    assert "type = AutoInterface" in r
    assert "devices = wlan1" in r
    # TCPServer on br-lan.
    assert "type = TCPServerInterface" in r
    assert "device = br-lan" in r
    assert "listen_port = 4242" in r
    # Entry-node uplink.
    assert "type = TCPClientInterface" in r
    # Interface modes: entry node is boundary, local interfaces internal,
    # so public-network announces are not flooded into the mesh/LAN.
    assert "mode = boundary" in r
    assert r.count("mode = internal") == 2
    assert "target_host = 173.230.150.24" in r
    assert "target_port = 4243" in r
    # KISS off by default.
    assert "KISSInterface" not in r


def test_reticulum_config_minimal():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "reticulum": {
            "transport": False,
            "tcp_server": False,
            "entry_node": False,
            "kiss_enabled": True,
            "kiss_port": "/dev/ttyUSB0",
        },
    })
    r = render_all(cfg)["/home/natak/.reticulum/config"]
    assert "enable_transport = No" in r
    assert "TCPServerInterface" not in r
    assert "TCPClientInterface" not in r
    assert "type = AutoInterface" in r          # still on by default
    assert "type = KISSInterface" in r
    assert "port = /dev/ttyUSB0" in r


def test_meshtasticd_config():
    m = rendered()["/etc/meshtasticd/config.yaml"]
    # RAK6421 slot1 LoRa pin block inlined (defaults).
    assert "Module: sx1262" in m
    assert "IRQ: 22" in m
    assert "Reset: 16" in m
    assert "Busy: 24" in m
    assert "spidev: spidev0.0" in m
    # UART GPS on the Pi GPIO header.
    assert "SerialPath: /dev/ttyS0" in m
    assert "APIPort: 4403" in m


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
    assert "ufw allow in on tailscale0" in m
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


def test_web_auth_in_nginx():
    n = rendered()["/etc/nginx/sites-available/nucleus"]
    # Basic auth enabled, trusted source nets skip it (satisfy any).
    assert "satisfy any;" in n
    assert "auth_basic_user_file /etc/nginx/nucleus.htpasswd;" in n
    assert "allow 127.0.0.1;" in n
    assert "allow 10.20.1.0/24;" in n     # mesh subnet
    assert "allow 10.20.9.0/24;" in n     # br-lan (node id 9)
    assert "allow 100.64.0.0/10;" in n    # tailscale
    assert "deny all;" in n
    assert "limit_req_zone" in n


def test_htpasswd_rendered():
    h = rendered()["/etc/nginx/nucleus.htpasswd"]
    # Deterministic {SHA} scheme, user admin, from default password.
    assert h.startswith("admin:{SHA}") or "\nadmin:{SHA}" in h


def test_htpasswd_deterministic():
    # Same password -> identical file (apply idempotence).
    assert render_all(CFG)["/etc/nginx/nucleus.htpasswd"] == \
        render_all(CFG)["/etc/nginx/nucleus.htpasswd"]


def test_meshup_web_over_eth0():
    m = rendered()["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    assert "ufw allow in on eth0 to any port 80 proto tcp" in m
    assert "ufw allow in on eth0 to any port 443 proto tcp" in m


def test_meshup_web_over_eth0_disabled():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "web": {"eth0_access": False},
    })
    m = render_all(cfg)["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    assert "ufw allow in on eth0 to any port 80 proto tcp" not in m
    assert "ufw allow in on eth0 to any port 443 proto tcp" not in m
    # The reset rebuilds the ruleset, so any 80/443 rule left from an earlier
    # apply is removed rather than lingering.
    assert "ufw --force reset" in m
    # ssh still allowed regardless.
    assert "ufw allow in on eth0 to any port 22 proto tcp" in m


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
