"""nucleusd.api — config API security regression tests.

Two guarantees the raw config API must keep, both reachable by any client on a
trusted network (mesh/AP/Tailscale) since the API itself has no login:

  * CSRF guard: a state-changing request (POST/PUT/...) carrying a cross-origin
    `Origin` header is refused. Browsers always attach Origin on such requests,
    so a malicious page cannot drive the API through a trusted client's browser.
    Non-browser clients (curl/scripts/peer nodes) send no Origin and are
    unaffected — the machine contract keeps working.
  * Password redaction: GET /api/v1/config never returns the eth0 web password
    (nor its derived htpasswd hash), and a blank password on PUT means "keep the
    stored one" so a round-trip save can't silently wipe it.
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
    """Point config I/O at a temp config.yaml and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    monkeypatch.setattr(cfgio, "CONFIG_PATH", p)
    return p


# --- CSRF guard -------------------------------------------------------------

def test_cross_origin_write_is_refused():
    # A browser POST from another site carries a mismatched Origin.
    r = client.post(
        "/api/v1/apply?dry_run=true",
        headers={"Origin": "http://evil.example", "Host": "0042-nucleus.local"},
    )
    assert r.status_code == 403
    assert "cross-origin" in r.json()["detail"]


def test_same_origin_write_is_allowed(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    # Origin host matches the Host it was addressed to -> allowed through.
    r = client.put(
        "/api/v1/config",
        json=cfgio.load().model_dump(mode="json"),
        headers={"Origin": "https://0042-nucleus.local",
                 "Host": "0042-nucleus.local"},
    )
    assert r.status_code == 200


def test_write_without_origin_is_allowed(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    # No Origin header at all == a non-browser client (curl, peer node, script).
    r = client.put("/api/v1/config", json=cfgio.load().model_dump(mode="json"))
    assert r.status_code == 200


def test_get_is_never_blocked_by_origin():
    # Reads are exempt even from a foreign origin (they change nothing).
    r = client.get("/api/v1/version", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200


# --- Password redaction / keep-on-blank -------------------------------------

def test_get_config_redacts_password(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    d = client.get("/api/v1/config").json()
    assert d["web"]["password"] == ""
    # The derived htpasswd hash of the password must not leak either.
    assert "web_htpasswd" not in d["_derived"]


def test_put_blank_password_keeps_stored(tmp_path, monkeypatch):
    p = _seed(tmp_path, monkeypatch)
    body = cfgio.load().model_dump(mode="json")
    body["web"]["password"] = ""          # what a redacted round-trip sends back
    r = client.put("/api/v1/config", json=body)
    assert r.status_code == 200
    monkeypatch.setattr(cfgio, "CONFIG_PATH", p)
    assert cfgio.load().web.password == "s3cr3t-web-pw"


def test_put_new_password_is_persisted(tmp_path, monkeypatch):
    p = _seed(tmp_path, monkeypatch)
    body = cfgio.load().model_dump(mode="json")
    body["web"]["password"] = "brand-new-pw"
    r = client.put("/api/v1/config", json=body)
    assert r.status_code == 200
    monkeypatch.setattr(cfgio, "CONFIG_PATH", p)
    assert cfgio.load().web.password == "brand-new-pw"
