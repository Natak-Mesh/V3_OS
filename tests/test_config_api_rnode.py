"""nucleusd.api — RNode config endpoint tests.

The web UI's RNode page edits reticulum.rnode_* through the plain config API:
it reads field bounds/options from GET /api/v1/config/schema (the schema.py
contract, exposed machine-readable so the UI keeps no second copy of the rules)
and persists values with PUT /api/v1/config. These pin that contract:

  * the schema endpoint exposes the RNode bounds + bandwidth enum, and
  * a PUT round-trips valid rnode_* values but 422s an out-of-range one.
"""

import textwrap

from fastapi.testclient import TestClient

from nucleusd import config as cfgio
from nucleusd.api import app

client = TestClient(app)


_CONFIG = """
    node:
      id: 7
    mesh:
      subnet_prefix: 10.20.1
      password: pw123456
    ap:
      password: ap123456
    web:
      user: admin
      password: s3cr3t-web-pw
"""


def _seed(tmp_path, monkeypatch, body=_CONFIG):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    monkeypatch.setattr(cfgio, "CONFIG_PATH", p)
    return p


def test_schema_endpoint_exposes_rnode_bounds():
    r = client.get("/api/v1/config/schema")
    assert r.status_code == 200
    props = r.json()["$defs"]["ReticulumConfig"]["properties"]
    assert props["rnode_txpower"]["maximum"] == 22
    assert props["rnode_frequency"]["minimum"] == 150_000_000
    assert props["rnode_frequency"]["maximum"] == 960_000_000
    # Bandwidth is an enum of the chip's discrete steps (the UI's dropdown).
    assert 250000 in props["rnode_bandwidth"]["enum"]
    assert 200000 not in props["rnode_bandwidth"]["enum"]


def test_put_rnode_round_trips(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    cfg = client.get("/api/v1/config").json()
    cfg.pop("_derived", None)
    cfg["reticulum"]["rnode_frequency"] = 868_000_000
    cfg["reticulum"]["rnode_bandwidth"] = 125000
    cfg["reticulum"]["rnode_txpower"] = 17
    assert client.put("/api/v1/config", json=cfg).status_code == 200
    saved = client.get("/api/v1/config").json()["reticulum"]
    assert saved["rnode_frequency"] == 868_000_000
    assert saved["rnode_bandwidth"] == 125000
    assert saved["rnode_txpower"] == 17


def test_put_rnode_out_of_range_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    cfg = client.get("/api/v1/config").json()
    cfg.pop("_derived", None)
    cfg["reticulum"]["rnode_txpower"] = 99
    assert client.put("/api/v1/config", json=cfg).status_code == 422
