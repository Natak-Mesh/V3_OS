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
    assert c.br_lan_ip == "10.20.12.1"
    assert c.node.hostname == "0009-nucleus"
    assert c.ap_name == "0009-nucleus-ap"


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
    with pytest.raises(ValidationError):
        cfg(mesh={"password": "52235223", "subnet_prefix": "10.20.5"},
            br_lan={"subnet_prefix": "10.20.5"})
