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


def test_tak_variant_rejects_unknown():
    with pytest.raises(ValidationError):
        cfg(tak={"variant": "opentakserver"})


def test_derived_addressing():
    c = cfg()
    assert c.mesh_ip == "10.20.1.9"
    assert c.br_lan_ip == "10.20.9.1"          # per-node: 10.20.<id>.1
    assert c.br_lan_subnet == "10.20.9.0/24"
    assert c.eth0_lan_ip == "10.10.9.1"        # per-node eth0 LAN gateway
    assert c.node.hostname == "0009-nucleus"
    assert c.ap_name == "0009-nucleus-ap"


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
    assert r.kiss_enabled is False


def test_reticulum_loglevel_bounds():
    with pytest.raises(ValidationError):
        cfg(reticulum={"loglevel": 8})


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
    assert cfg().meshtastic.tx_min_interval_secs == 30


def test_tx_min_interval_bounds_accepted():
    assert cfg(meshtastic={"tx_min_interval_secs": 0}).meshtastic.tx_min_interval_secs == 0
    assert cfg(meshtastic={"tx_min_interval_secs": 3600}).meshtastic.tx_min_interval_secs == 3600


def test_tx_min_interval_out_of_range_rejected():
    with pytest.raises(ValidationError):
        cfg(meshtastic={"tx_min_interval_secs": -1})
    with pytest.raises(ValidationError):
        cfg(meshtastic={"tx_min_interval_secs": 3601})
