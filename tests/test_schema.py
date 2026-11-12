"""Schema + derived-value tests. Run off-box: `pytest` from repo root."""

import pytest
from pydantic import ValidationError

from nucleusd.schema import NucleusConfig

BASE = {
    "node": {"id": 9},
    "mesh": {"password": "52235223"},
    "ap": {"password": "52235223"},
}


def cfg(**over):
    import copy
    d = copy.deepcopy(BASE)
    for k, v in over.items():
        d[k] = {**d.get(k, {}), **v}
    return NucleusConfig.model_validate(d)


def test_tak_defaults_off():
    # Most nodes never touch TAK: the block defaults to variant=none and is
    # inert (no apply-loop targets, script no-ops).
    c = cfg()
    assert c.tak.variant == "none"
    assert c.tak.enrollment_validity_days == 365
    assert c.tak.keystore_pass == "atakatak"
    assert c.tak.cert.organization == "NATAK"


def test_tak_ca_names_derive_from_hostname():
    # CA common names are derived (no spaces) from the hostname, mirroring the
    # rest of the derived-addressing scheme.
    c = cfg()
    assert c.tak_root_ca_name == "0009-nucleus-root"
    assert c.tak_intermediate_ca_name == "0009-nucleus-ca"


def test_tak_variant_accepts_opentakserver():
    # OpenTAKServer is the either/or alternative to the official server.
    c = cfg(tak={"variant": "opentakserver"})
    assert c.tak.variant == "opentakserver"


def test_tak_variant_rejects_unknown():
    with pytest.raises(ValidationError):
        cfg(tak={"variant": "freetakserver"})


def test_derived_addressing():
    c = cfg()
    assert c.mesh_ip == "10.20.1.9"
    assert c.br_lan_ip == "10.20.9.1"          # per-node: 10.20.<id>.1
    assert c.br_lan_subnet == "10.20.9.0/24"
    assert c.eth0_lan_ip == "10.10.9.1"        # per-node eth0 LAN gateway
    assert c.node.hostname == "0009-nucleus"
    assert c.ap_name == "0009-nucleus-ap"


def test_mesh_enabled_defaults_on():
    # RF-control Phase 1: wlan1 mesh is on by default and persists in config.
    assert cfg().mesh.enabled is True
    assert cfg(mesh={"enabled": False}).mesh.enabled is False


def test_meshtastic_tx_enabled_defaults_on():
    # RF-control Phase 1: LoRa TX is on by default; False = receive-only.
    assert cfg().meshtastic.tx_enabled is True
    assert cfg(meshtastic={"tx_enabled": False}).meshtastic.tx_enabled is False


def test_br_lan_prefix_override():
    c = cfg(br_lan={"subnet_prefix": "10.20.200"})
    assert c.br_lan_ip == "10.20.200.1"


def test_reticulum_defaults():
    r = cfg().reticulum
    assert r.enabled is True
    assert r.transport is True
    assert r.auto_device == "wlan1"
    assert r.tcp_server_port == 4242
    assert r.entry_node_host == "173.230.150.24"
    assert r.entry_node_port == 4243
    # RNode on by default, with sane radio defaults.
    assert r.rnode_enabled is True
    assert r.rnode_frequency == 914875000
    assert r.rnode_bandwidth == 250000
    assert r.rnode_txpower == 22
    assert r.rnode_spreadingfactor == 9
    assert r.rnode_codingrate == 5


def test_reticulum_loglevel_bounds():
    with pytest.raises(ValidationError):
        cfg(reticulum={"loglevel": 8})


def test_reticulum_rnode_bounds():
    with pytest.raises(ValidationError):
        cfg(reticulum={"rnode_txpower": 23})
    with pytest.raises(ValidationError):
        cfg(reticulum={"rnode_spreadingfactor": 13})
    with pytest.raises(ValidationError):
        cfg(reticulum={"rnode_codingrate": 9})


def test_reticulum_rnode_frequency_bounds():
    # Outside the SX1262 range is rejected; an in-range value is accepted.
    with pytest.raises(ValidationError):
        cfg(reticulum={"rnode_frequency": 100_000_000})
    with pytest.raises(ValidationError):
        cfg(reticulum={"rnode_frequency": 1_000_000_000})
    assert cfg(reticulum={"rnode_frequency": 868_000_000}).reticulum.rnode_frequency == 868_000_000


def test_reticulum_rnode_bandwidth_must_be_a_known_step():
    # Bandwidth is a Literal of the chip's steps; an arbitrary value is rejected.
    with pytest.raises(ValidationError):
        cfg(reticulum={"rnode_bandwidth": 200_000})
    assert cfg(reticulum={"rnode_bandwidth": 125000}).reticulum.rnode_bandwidth == 125000


def test_usb_devices_registry_unique_ids():
    """No USB id may be claimed by two roles (ambiguous /dev/nucleus link)."""
    from nucleusd.schema import USB_DEVICES

    seen: dict[tuple[str, str], str] = {}
    for role, dev in USB_DEVICES.items():
        for usb_id in dev["ids"]:
            assert usb_id not in seen, f"{usb_id} claimed by {seen.get(usb_id)} and {role}"
            seen[usb_id] = role
    # RAK4631 is the only supported RNode for now (a tty-kind serial device).
    assert USB_DEVICES["rnode"]["kind"] == "tty"
    assert USB_DEVICES["rnode"]["ids"] == [("239a", "8029")]
    # HaLow is a net-kind device renamed to the fixed halow0 ifname.
    assert USB_DEVICES["halow"]["kind"] == "net"
    assert USB_DEVICES["halow"]["ids"] == [("325b", "8100")]
    assert USB_DEVICES["halow"]["ifname"] == "halow0"


def test_web_defaults():
    w = cfg().web
    assert w.user == "admin"
    assert w.password == "52235223"
    assert w.eth0_access is True


def test_web_short_password_rejected():
    with pytest.raises(ValidationError):
        cfg(web={"password": "12345"})


def test_firewall_defaults():
    assert cfg().firewall.enabled is True


def test_firewall_disabled_in_context():
    c = cfg(firewall={"enabled": False})
    assert c.firewall.enabled is False
    assert c.render_context()["firewall_enabled"] is False


def test_web_htpasswd_deterministic():
    # {SHA} scheme, stable for a given password (apply idempotence).
    c = cfg()
    assert c.web_htpasswd.startswith("admin:{SHA}")
    assert cfg().web_htpasswd == c.web_htpasswd


def test_web_trusted_cidrs_derive():
    c = cfg()
    assert "10.20.1.0/24" in c.web_trusted_cidrs   # mesh
    assert "10.20.9.0/24" in c.web_trusted_cidrs   # br-lan (id 9)
    assert "100.64.0.0/10" in c.web_trusted_cidrs  # tailscale


def test_frequency_matches_channel():
    assert cfg(mesh={"password": "52235223", "channel": 3}).mesh.frequency == 2422


def test_ipv6_ll_deterministic_and_unique():
    c = cfg()
    assert c.mesh_ipv6_ll.startswith("fe80::")
    assert c.mesh_ipv6_ll != c.br_lan_ipv6_ll
    # deterministic across instances
    assert cfg().mesh_ipv6_ll == c.mesh_ipv6_ll


def test_node_id_bounds():
    with pytest.raises(ValidationError):
        cfg(node={"id": 0})
    with pytest.raises(ValidationError):
        cfg(node={"id": 255})


def test_short_password_rejected():
    with pytest.raises(ValidationError):
        cfg(mesh={"password": "short"})


def test_id_from_hostname(monkeypatch):
    monkeypatch.setattr("nucleusd.schema.socket.gethostname", lambda: "0042-nucleus")
    c = NucleusConfig.model_validate({
        "node": {},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
    })
    assert c.node.id == 42
    assert c.mesh_ip == "10.20.1.42"
    assert c.node.hostname == "0042-nucleus"


def test_bad_hostname_rejected(monkeypatch):
    monkeypatch.setattr("nucleusd.schema.socket.gethostname", lambda: "raspberrypi")
    with pytest.raises(ValidationError):
        NucleusConfig.model_validate({
            "node": {},
            "mesh": {"password": "52235223"},
            "ap": {"password": "52235223"},
        })


def test_explicit_id_overrides_hostname(monkeypatch):
    monkeypatch.setattr("nucleusd.schema.socket.gethostname", lambda: "0042-nucleus")
    c = NucleusConfig.model_validate({
        "node": {"id": 7},
        "mesh": {"password": "52235223"},
        "ap": {"password": "52235223"},
    })
    assert c.node.id == 7


def test_subnet_collision_rejected():
    # node.id == 1 makes the default br-lan (10.20.1) collide with the mesh.
    with pytest.raises(ValidationError):
        cfg(node={"id": 1})


def test_tx_min_interval_default():
    assert cfg().meshtastic.tx_min_interval_secs == 2


def test_tx_min_interval_bounds_accepted():
    assert cfg(meshtastic={"tx_min_interval_secs": 0}).meshtastic.tx_min_interval_secs == 0
    assert cfg(meshtastic={"tx_min_interval_secs": 3600}).meshtastic.tx_min_interval_secs == 3600


def test_tx_min_interval_out_of_range_rejected():
    with pytest.raises(ValidationError):
        cfg(meshtastic={"tx_min_interval_secs": -1})
    with pytest.raises(ValidationError):
        cfg(meshtastic={"tx_min_interval_secs": 3601})


def test_modem73_mode_default_and_derived_bitrate():
    from nucleusd.schema import MODEM73_DEFAULT_MODE, MODEM73_MODES
    c = cfg()
    # Default is modem73's own OFDM default (QPSK 1/2).
    assert c.reticulum.modem73_mode == MODEM73_DEFAULT_MODE == "qpsk_1_2"
    # The RNS bitrate and the launch flags are BOTH derived from the one
    # registry entry, so the modem and Reticulum can never disagree.
    ctx = c.render_context()
    sel = MODEM73_MODES[c.reticulum.modem73_mode]
    assert ctx["reti_modem73_bitrate"] == sel["bitrate"] == 1577
    assert ctx["modem73_mode_flags"] == "--modem ofdm --modulation QPSK --rate 1/2"


def test_modem73_mode_selection_derives_matching_values():
    c = cfg(reticulum={"modem73_mode": "qam4096_5_6"})
    ctx = c.render_context()
    assert ctx["modem73_mode_flags"] == "--modem ofdm --modulation QAM4096 --rate 5/6"
    assert ctx["reti_modem73_bitrate"] == 15959
    c = cfg(reticulum={"modem73_mode": "rdm_1200"})
    ctx = c.render_context()
    assert ctx["modem73_mode_flags"] == "--robust-mode RDM-1200"
    assert ctx["reti_modem73_bitrate"] == 1150


def test_modem73_every_mode_fits_a_full_rns_packet():
    """Regression: with --no-frag modem73 TRUNCATES a KISS frame larger than the
    PHY payload (BPSK 1/2 = 256 B silently cut 500 B RNS packets). RNS refuses
    fixed_mtu < 500, so every registry mode must carry 500 B + 2 B prefix."""
    from nucleusd.schema import MODEM73_MIN_PAYLOAD, MODEM73_MODES
    assert MODEM73_MIN_PAYLOAD == 502
    for key, m in MODEM73_MODES.items():
        assert m["payload"] >= MODEM73_MIN_PAYLOAD, key


def test_modem73_every_mode_meets_airtime_budget():
    """Squad data link: a full 500 B packet must go out in < ~4 s (>= 1000 bps).
    Pins that the too-slow modes (BPSK, MFSK, slower RDM) stay out."""
    from nucleusd.schema import MODEM73_MODES
    for key, m in MODEM73_MODES.items():
        assert 500 * 8 / m["bitrate"] < 4.0, key
    assert "bpsk_1_2" not in MODEM73_MODES


def test_modem73_dropped_mode_migrates():
    # Node 0042's live config was bpsk_1_2 when it was dropped.
    assert cfg(reticulum={"modem73_mode": "bpsk_1_2"}).reticulum.modem73_mode == "qpsk_1_2"


def test_modem73_callsign_defaults_to_node_id():
    assert cfg().modem73_callsign == "0009"
    # The old placeholder default is treated as unset.
    assert cfg(reticulum={"modem73_callsign": "N0CALL"}).modem73_callsign == "0009"
    assert cfg(reticulum={"modem73_callsign": "  "}).modem73_callsign == "0009"
    assert cfg(reticulum={"modem73_callsign": "W1AW/P"}).modem73_callsign == "W1AW/P"
    assert cfg().render_context()["modem73_callsign"] == "0009"


def test_modem73_callsign_rejects_what_modem73_rejects():
    # modem73 aborts at startup on these (ModemConfig::valid_callsign).
    for bad in ("TEST-1", "TOOLONGCALL", "0042_x"):
        with pytest.raises(ValidationError):
            cfg(reticulum={"modem73_callsign": bad})


def test_modem73_gain_bounds():
    c = cfg()
    assert (c.reticulum.modem73_rx_gain, c.reticulum.modem73_tx_gain) == (15, 11)
    assert c.reticulum.modem73_tx_level == 5
    with pytest.raises(ValidationError):
        cfg(reticulum={"modem73_rx_gain": 36})
    with pytest.raises(ValidationError):
        cfg(reticulum={"modem73_tx_gain": 38})


def test_modem73_mode_rejects_unknown():
    with pytest.raises(ValidationError):
        cfg(reticulum={"modem73_mode": "qam9999_1_1"})


def test_modem73_mode_publishes_rate_metadata_in_schema():
    """The published JSON schema carries the per-mode bitrate (json_schema_extra)
    so the web UI can label the dropdown from the one contract, not a JS copy."""
    from nucleusd.schema import MODEM73_MODES, ReticulumConfig
    prop = ReticulumConfig.model_json_schema()["properties"]["modem73_mode"]
    assert prop["modes"] == MODEM73_MODES
    # Every enum value has matching rate metadata.
    assert set(prop["enum"]) == set(MODEM73_MODES)
    assert prop["modes"]["qpsk_1_2"]["bitrate"] == 1577


def test_modem73_mode_from_live_config():
    """Maps a real modem73 get_config (captured on 0042) back to a registry key."""
    from nucleusd.schema import modem73_mode_from_live
    from tests.modem73_fixtures import GET_CONFIG_BPSK, GET_CONFIG_RDM1200
    assert modem73_mode_from_live(GET_CONFIG_BPSK) is None  # dropped mode
    assert modem73_mode_from_live({**GET_CONFIG_BPSK, "modulation": "QPSK"}) == "qpsk_1_2"
    assert modem73_mode_from_live(GET_CONFIG_RDM1200) == "rdm_1200"
