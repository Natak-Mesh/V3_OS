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
    assert len(r) == 21
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
    # No public entry-node TCPClient uplink: peering is over eth0 via the
    # AutoInterface now (dropped in the eth0-peering change). The boundary Entry
    # Node is gone; the only TCPClientInterface present is the modem73 soundmodem.
    assert "mode = boundary" not in r
    assert "Entry Node" not in r
    assert "target_host = 173.230.150.24" not in r
    # Both local interfaces (AutoInterface + TCPServer) are internal so
    # announces are not flooded.
    assert r.count("mode = internal") == 2
    # The unused serial KISSInterface was removed; modem73 is the only KISS path.
    assert "KISSInterface" not in r
    # RNode on by default, bound to the stable udev symlink.
    assert "type = RNodeInterface" in r
    assert "port = /dev/nucleus/rnode" in r
    assert "frequency = 914875000" in r
    # modem73 soundmodem on by default: built-in TCPClientInterface with KISS
    # framing to the local modem73 KISS-on-TCP socket.
    assert "[[MODEM73 Soundmodem]]" in r
    assert "type = TCPClientInterface" in r
    assert "kiss_framing = True" in r
    assert "target_host = 127.0.0.1" in r
    assert "target_port = 8001" in r
    assert "fixed_mtu = 500" in r
    # Bitrate is derived from the default OFDM mode (qpsk_1_2 → 1577 bps), not a
    # placeholder; it matches the modem73 launch flags so RNS sizes framing and
    # the announce rate-cap to the real on-air rate.
    assert "bitrate = 1577" in r


def test_reticulum_config_minimal():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "reticulum": {
            "transport": False,
            "tcp_server": False,
            "entry_node": False,
            "modem73_enabled": False,
        },
    })
    r = render_all(cfg)["/home/natak/.reticulum/config"]
    assert "enable_transport = No" in r
    assert "TCPServerInterface" not in r
    assert "TCPClientInterface" not in r
    assert "type = AutoInterface" in r          # still on by default


def test_reticulum_legacy_kiss_keys_ignored():
    """Regression: the unused serial KISS TNC interface was removed. A live
    config.yaml written before the removal still carries kiss_* keys; it must
    still validate, and must not render a KISSInterface."""
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "reticulum": {
            "kiss_enabled": False,
            "kiss_port": "/dev/rfcomm0",
            "kiss_speed": 115200,
        },
    })
    assert not hasattr(cfg.reticulum, "kiss_enabled")
    assert "kiss_enabled" not in cfg.model_dump()["reticulum"]
    r = render_all(cfg)["/home/natak/.reticulum/config"]
    assert "KISSInterface" not in r


def test_reticulum_modem73_disabled():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "reticulum": {"modem73_enabled": False},
    })
    r = render_all(cfg)["/home/natak/.reticulum/config"]
    assert "MODEM73 Soundmodem" not in r
    assert "kiss_framing" not in r


def test_reticulum_rnode_enabled():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "reticulum": {
            "rnode_enabled": True,
            "rnode_frequency": 868000000,
            "rnode_bandwidth": 125000,
            "rnode_txpower": 17,
            "rnode_spreadingfactor": 8,
            "rnode_codingrate": 6,
        },
    })
    r = render_all(cfg)["/home/natak/.reticulum/config"]
    assert "type = RNodeInterface" in r
    # Bound to the stable udev symlink, never a raw /dev/ttyACM*.
    assert "port = /dev/nucleus/rnode" in r
    assert "frequency = 868000000" in r
    assert "bandwidth = 125000" in r
    assert "txpower = 17" in r
    assert "spreadingfactor = 8" in r
    assert "codingrate = 6" in r


def test_modem73_launch_script():
    r = rendered()["/opt/nucleus/bin/nucleus-modem73-up.sh"]
    assert "/usr/bin/modem73" in r
    assert "--headless" in r
    # Audio by name (stable), not a card index.
    assert "--device 'USB Audio Device, USB Audio'" in r
    # Serial PTT on the stable udev symlink, keyed on the RTS line (DigiRig).
    assert "--ptt com" in r
    assert "--com-port /dev/nucleus/modem" in r
    assert "--com-line rts" in r
    # KISS/control ports match the RNS interface constants.
    assert "--port 8001" in r
    assert "--control-port 8073" in r
    # Default OFDM mode (qpsk_1_2) is passed explicitly so the on-air rate is known
    # and matches the RNS bitrate rather than relying on the modem's own default.
    assert "--modem ofdm" in r
    assert "--modulation QPSK" in r
    assert "--rate 1/2" in r


def test_modem73_mode_selection_matches_bitrate():
    """A selected OFDM mode drives BOTH the launch flags and the RNS bitrate from
    one registry entry, so the modem and Reticulum can never disagree."""
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "reticulum": {"modem73_mode": "qam256_5_6"},
    })
    r = render_all(cfg)
    # Launch script carries the matching modulation + code rate.
    script = r["/opt/nucleus/bin/nucleus-modem73-up.sh"]
    assert "--modulation QAM256" in script
    assert "--rate 5/6" in script
    # Reticulum interface is told the exact modem-reported on-air bitrate for that
    # mode (QAM256 5/6 → 10500 bps), not the default and not a placeholder.
    reti = r["/home/natak/.reticulum/config"]
    assert "bitrate = 10500" in reti
    assert "bitrate = 1577" not in reti


def test_modem73_launch_script_custom():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "reticulum": {
            "modem73_audio_device": "Some Other Card, USB Audio",
            "modem73_ptt_line": "dtr",
            "modem73_callsign": "TEST-1",
            "modem73_tx_level": 80,
        },
    })
    r = render_all(cfg)["/opt/nucleus/bin/nucleus-modem73-up.sh"]
    assert "--device 'Some Other Card, USB Audio'" in r
    assert "--com-line dtr" in r
    assert "--callsign 'TEST-1'" in r
    assert "--tx-level 80" in r


def test_modem73_csma_sync_mode():
    """Regression: radios run squelch-off, so receiver hiss is always present.
    modem73's default 'threshold' CSMA treated that hiss as a busy channel and
    never transmitted; 'sync' only counts a real modem signal as busy."""
    r = rendered()["/opt/nucleus/bin/nucleus-modem73-up.sh"]
    assert "--csma-mode sync" in r
    # VHF/UHF FM radios: VHF CSMA timing profile, not modem73's 'hf' default.
    assert "--csma-band vhf" in r
    assert "threshold" not in r.split("exec /usr/bin/modem73", 1)[1]


def test_usb_udev_rule():
    r = rendered()["/etc/udev/rules.d/60-nucleus-usb.rules"]
    # RAK4631 (tty kind) → /dev/nucleus/rnode, with ModemManager/mtp-probe left off.
    assert 'SUBSYSTEM=="tty"' in r
    assert 'ATTRS{idVendor}=="239a"' in r
    assert 'ATTRS{idProduct}=="8029"' in r
    assert 'SYMLINK+="nucleus/rnode"' in r
    assert 'ENV{ID_MM_DEVICE_IGNORE}="1"' in r
    assert 'ENV{MTP_NO_PROBE}="1"' in r
    # modem73 serial/PTT adapter (CP210x, tty kind) → /dev/nucleus/modem, and
    # device-started (tagged for systemd) so its service can't race the symlink.
    assert 'ATTRS{idVendor}=="10c4"' in r
    assert 'ATTRS{idProduct}=="ea60"' in r
    assert 'SYMLINK+="nucleus/modem"' in r
    modem_line = next(l for l in r.splitlines() if 'SYMLINK+="nucleus/modem"' in l)
    assert 'ENV{SYSTEMD_WANTS}="nucleus-modem73.service"' in modem_line
    # rnode is just an rnsd interface (no service key) — its tty rule stays plain.
    rnode_line = next(l for l in r.splitlines() if 'SYMLINK+="nucleus/rnode"' in l)
    assert "SYSTEMD_WANTS" not in rnode_line
    # HaLow (net kind): Morse MM8108 renamed to halow0 + service wanted.
    assert 'SUBSYSTEM=="net"' in r
    assert 'ATTRS{idVendor}=="325b"' in r
    assert 'ATTRS{idProduct}=="8100"' in r
    assert 'NAME="halow0"' in r
    assert 'ENV{SYSTEMD_WANTS}="nucleus-halow.service"' in r
    # The net rule must NOT be gated on ACTION=="add": the interface rename
    # emits a second "move" uevent and an add-only rule drops SYSTEMD_WANTS on
    # it, so systemd never pulls in nucleus-halow on plug-in. Regression guard.
    net_line = next(ln for ln in r.splitlines() if 'NAME="halow0"' in ln)
    assert "ACTION==" not in net_line


def test_meshtasticd_config():
    m = rendered()["/etc/meshtasticd/config.yaml"]
    # RAK6421 slot2 LoRa pin block inlined (defaults).
    assert "Module: sx1262" in m
    assert "IRQ: 18" in m
    assert "Reset: 24" in m
    assert "Busy: 19" in m
    assert "spidev: spidev0.1" in m
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
    # UFW and eth0 WAN NAT moved to nucleus-firewall.sh — mesh-up no longer has them.
    assert "masquerade" not in m
    assert "ufw" not in m


def test_meshup_preserves_link_local_across_bounce():
    # rfkill toggling the mesh bounces wlan1; the bring-up must keep the fe80
    # link-local (Babel needs it) and re-assert networkd after the join.
    m = rendered()["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    assert "net.ipv6.conf.wlan1.keep_addr_on_down=1" in m
    assert "networkctl reconfigure wlan1" in m


FIREWALL_PATH = "/opt/nucleus/bin/nucleus-firewall.sh"


def test_firewall_script_header():
    # UFW now lives in its own radio-independent script.
    f = rendered()[FIREWALL_PATH]
    assert f.startswith("#!/bin/bash")
    assert "set -euo pipefail" in f


def test_firewall_wan_nat_moved_here():
    # The eth0 WAN NAT masquerade moved out of mesh-up into the firewall script,
    # kept adjacent to the `ufw route ... out on eth0` rules it backs.
    f = rendered()[FIREWALL_PATH]
    assert 'nft add rule ip nat POSTROUTING oifname "eth0" masquerade' in f
    assert f.index("masquerade") < f.index("ufw route allow in on br-lan out on eth0")


def test_firewall_reti_udp_ports():
    # The three Reticulum AutoInterface UDP ports must be opened on eth0 so wired
    # peering forms; these are the rules that silently never applied when UFW was
    # coupled to the wlan1 mesh bring-up.
    f = rendered()[FIREWALL_PATH]
    assert "ufw allow in on eth0 to any port 29716 proto udp" in f
    assert "ufw allow in on eth0 to any port 29717 proto udp" in f
    assert "ufw allow in on eth0 to any port 42671 proto udp" in f


def test_firewall_rules():
    m = rendered()[FIREWALL_PATH]
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


def test_firewall_reset_before_allow():
    # config.yaml is the single source of truth: the ruleset is rebuilt every
    # run, so a reset must precede the first `ufw allow` (no stale rules linger).
    m = rendered()[FIREWALL_PATH]
    assert "ufw --force reset" in m
    assert m.index("ufw --force reset") < m.index("ufw allow")
    # ssh-on-eth0 is added first, right after the reset, so admin access over
    # ethernet is never left dangling.
    assert m.index("ufw allow in on eth0 to any port 22 proto tcp") < \
        m.index("ufw allow in on wlan1")
    # Reset leaves timestamped rule-file backups; they must be cleaned up.
    assert "rm -f /etc/ufw/*.rules.[0-9]*_[0-9]*" in m


def test_firewall_disabled():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "firewall": {"enabled": False},
    })
    m = render_all(cfg)[FIREWALL_PATH]
    # Firewall off: ufw is disabled and no rules are rendered at all.
    assert "ufw --force disable" in m
    assert "ufw allow" not in m
    assert "ufw route" not in m
    assert "ufw --force enable" not in m
    assert "ufw --force reset" not in m


TAK_ETH0_RULES = (
    "ufw allow in on eth0 to any port 8443 proto tcp",
    "ufw allow in on eth0 to any port 8089 proto tcp",
    "ufw allow in on eth0 to any port 8446 proto tcp",
    "ufw allow in on eth0 to any port 8090 proto udp",
)


def test_firewall_tak_ports_gated_on_installed_package():
    m = rendered()[FIREWALL_PATH]
    # Runtime dpkg check (not config) decides whether TAK ports open on eth0.
    check = ("if [ \"$(dpkg-query -W -f='${Status}' takserver 2>/dev/null)\""
             " = \"install ok installed\" ]; then")
    assert check in m
    for rule in TAK_ETH0_RULES:
        assert rule in m
        # Inside the dpkg guard, after the reset, before ufw is enabled.
        assert m.index(check) < m.index(rule) < m.index("ufw --force enable")
        assert m.index("ufw --force reset") < m.index(rule)


MEDIAMTX_ETH0_RULES = (
    "ufw allow in on eth0 to any port 8554 proto tcp",
    "ufw allow in on eth0 to any port 8000 proto udp",
    "ufw allow in on eth0 to any port 8001 proto udp",
    "ufw allow in on eth0 to any port 8890 proto udp",
)


def test_firewall_mediamtx_ports_gated_on_enabled_unit():
    m = rendered()[FIREWALL_PATH]
    # Runtime systemd check decides whether MediaMTX ports open on eth0.
    check = "if systemctl is-enabled --quiet mediamtx.service 2>/dev/null; then"
    assert check in m
    for rule in MEDIAMTX_ETH0_RULES:
        assert rule in m
        # Inside the systemd guard, after the reset, before ufw is enabled.
        assert m.index(check) < m.index(rule) < m.index("ufw --force enable")
        assert m.index("ufw --force reset") < m.index(rule)


OTS_ETH0_RULES = (
    "ufw allow in on eth0 to any port 8444 proto tcp comment 'ots web ui'",
    "ufw allow in on eth0 to any port 8443 proto tcp comment 'ots marti api'",
    "ufw allow in on eth0 to any port 8446 proto tcp comment 'ots cert enrollment'",
    "ufw allow in on eth0 to any port 8089 proto tcp comment 'ots client tls'",
    "ufw allow in on eth0 to any port 8883 proto tcp comment 'ots mqtt tls'",
    "ufw allow in on eth0 to any port 8322 proto tcp comment 'ots rtsps'",
    "ufw allow in on eth0 to any port 1936 proto tcp comment 'ots rtmps'",
)


def test_firewall_ots_ports_gated_on_enabled_unit():
    m = rendered()[FIREWALL_PATH]
    # Runtime systemd check decides whether OpenTAKServer ports open on eth0.
    check = "if systemctl is-enabled --quiet opentakserver.service 2>/dev/null; then"
    assert check in m
    for rule in OTS_ETH0_RULES:
        assert rule in m
        # Inside the systemd guard, after the reset, before ufw is enabled.
        assert m.index(check) < m.index(rule) < m.index("ufw --force enable")
        assert m.index("ufw --force reset") < m.index(rule)
    # Unauthenticated plain-HTTP port is never opened on eth0.
    assert "port 8082" not in m


def test_firewall_tak_ports_absent_when_firewall_disabled():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "firewall": {"enabled": False},
    })
    m = render_all(cfg)[FIREWALL_PATH]
    assert "dpkg-query" not in m
    assert "mediamtx" not in m
    assert "opentakserver" not in m
    for rule in TAK_ETH0_RULES + MEDIAMTX_ETH0_RULES + OTS_ETH0_RULES:
        assert rule not in m


def test_firewall_no_wan_route_in_lan_mode():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "eth0": {"mode": "lan", "static_ip": "10.10.9.1"},
    })
    m = render_all(cfg)[FIREWALL_PATH]
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


def test_firewall_web_over_eth0():
    m = rendered()[FIREWALL_PATH]
    assert "ufw allow in on eth0 to any port 80 proto tcp" in m
    assert "ufw allow in on eth0 to any port 443 proto tcp" in m


def test_firewall_web_over_eth0_disabled():
    cfg = NucleusConfig.model_validate({
        "node": {"id": 9},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
        "web": {"eth0_access": False},
    })
    m = render_all(cfg)[FIREWALL_PATH]
    assert "ufw allow in on eth0 to any port 80 proto tcp" not in m
    assert "ufw allow in on eth0 to any port 443 proto tcp" not in m
    # The reset rebuilds the ruleset, so any 80/443 rule left from an earlier
    # apply is removed rather than lingering.
    assert "ufw --force reset" in m
    # ssh still allowed regardless.
    assert "ufw allow in on eth0 to any port 22 proto tcp" in m


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


def test_mesh_up_is_rerunnable():
    # Fix B: the mesh-up script must kill any stale wpa_supplicant on wlan1 and
    # drop its control socket BEFORE the fresh join, so a re-run (apply without
    # reboot) does not abort under `set -euo pipefail`.
    m = rendered()["/opt/nucleus/bin/nucleus-mesh-up.sh"]
    kill = m.index("pkill -f 'wpa_supplicant .*-i wlan1'")
    assert "rm -f /var/run/wpa_supplicant/wlan1" in m
    # The cleanup must come before the actual join.
    assert kill < m.index("wpa_supplicant -B -i wlan1")


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
    # Router-less IPv6 so Reticulum AutoInterface peers over eth0 without an RA.
    assert "LinkLocalAddressing=ipv6" in e
    assert "IPv6AcceptRA=no" in e


def test_eth0_wan_mode_no_ipv6_overrides():
    # The wan branch must stay untouched (it is the DHCP/DNS path): the router-less
    # IPv6 lines belong only to lan mode.
    e = rendered()["/etc/systemd/network/40-eth0.network"]  # default = wan
    assert "DHCP=ipv4" in e
    assert "LinkLocalAddressing=ipv6" not in e
    assert "IPv6AcceptRA=no" not in e


def test_firewall_target_wired_and_decoupled():
    # The firewall renders to its own script+unit and is deliberately NOT part of
    # the mesh-radio network group (so it applies even with wlan1 RF-killed).
    from nucleusd.apply import TARGETS, NET_GROUP_ORDER, FIREWALL_UNIT
    fw = [t for t in TARGETS if str(t.dest) == "/opt/nucleus/bin/nucleus-firewall.sh"]
    assert len(fw) == 1
    assert fw[0].units == (FIREWALL_UNIT,)
    assert fw[0].mode == 0o755
    assert FIREWALL_UNIT not in NET_GROUP_ORDER
