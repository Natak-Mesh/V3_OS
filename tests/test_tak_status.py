"""nucleusd.api /api/v1/tak/status — official vs OpenTAKServer detection."""

import subprocess

from fastapi.testclient import TestClient

import nucleusd.api as api

client = TestClient(api.app)


class _Active:
    stdout = "active\n"


def _setup(monkeypatch, tmp_path, official=False, ots=False, certs=()):
    tak_dir = tmp_path / "opt-tak"
    ots_unit = tmp_path / "opentakserver.service"
    cert_dir = tmp_path / "tak-certs"
    if official:
        tak_dir.mkdir()
    if ots:
        ots_unit.write_text("[Unit]\n")
    if certs:
        cert_dir.mkdir()
        for c in certs:
            (cert_dir / c).write_bytes(b"x")
    monkeypatch.setattr(api, "TAK_DIR", tak_dir)
    monkeypatch.setattr(api, "OTS_UNIT", ots_unit)
    monkeypatch.setattr(api, "TAK_CERT_DIR", cert_dir)
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return _Active()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_tak_status_none_installed(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path)
    d = client.get("/api/v1/tak/status").json()
    assert d["installed"] is False
    assert d["variant"] is None
    assert d["service"] == "not installed"
    assert calls == []


def test_tak_status_official(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, official=True, certs=("webadmin.p12",))
    d = client.get("/api/v1/tak/status").json()
    assert d["installed"] is True
    assert d["variant"] == "official"
    assert d["unit"] == "takserver"
    assert d["service"] == "active"
    assert d["certs"] == ["webadmin.p12"]
    assert calls == [["systemctl", "is-active", "takserver.service"]]


def test_tak_status_opentakserver(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, ots=True, certs=("truststore-root.p12",))
    d = client.get("/api/v1/tak/status").json()
    assert d["installed"] is True
    assert d["variant"] == "opentakserver"
    assert d["unit"] == "opentakserver"
    assert d["service"] == "active"
    assert d["certs"] == ["truststore-root.p12"]
    assert calls == [["systemctl", "is-active", "opentakserver.service"]]


def test_tak_cert_download_serves_ots_truststore(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, ots=True, certs=("truststore-root.p12",))
    r = client.get("/api/v1/tak/certs/truststore-root.p12")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-pkcs12"
