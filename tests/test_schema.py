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


def test_firewall_defaults():
    assert cfg().firewall.enabled is True


def test_firewall_disabled_in_context():
    c = cfg(firewall={"enabled": False})
    assert c.firewall.enabled is False
    assert c.render_context()["firewall_enabled"] is False


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


def test_removed_sections_ignored_on_load():
    # Configs from a full Nucleus build still carry sections the drone build
    # dropped. They must load (ignored), not fail validation.
    c = NucleusConfig.model_validate({
        **BASE,
        "meshtastic": {"enabled": True},
        "messaging": {"enabled": True},
        "voice": {"enabled": True},
        "reticulum": {"enabled": True},
        "tak": {"variant": "none"},
    })
    dumped = c.model_dump()
    for gone in ("meshtastic", "messaging", "voice", "reticulum", "tak"):
        assert gone not in dumped
