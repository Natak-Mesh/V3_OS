"""Tests for nucleusd.tailscale — CLI wrapper parsing + command construction.

All tests mock `_run`/`shutil.which` so nothing shells out to a real tailscale.
Fixtures use the real `tailscale status --json` / `switch --list` formats.
"""

import json
import subprocess

import pytest

from nucleusd import tailscale as ts


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess(["tailscale"], rc, stdout=out, stderr=err)


# Trimmed but real-shape `tailscale status --json` for a connected node.
STATUS_JSON = json.dumps({
    "BackendState": "Running",
    "MagicDNSSuffix": "tail1234.ts.net",
    "CurrentTailnet": {"Name": "example.com", "MagicDNSSuffix": "tail1234.ts.net"},
    "Self": {
        "HostName": "0042-nucleus",
        "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"],
    },
    "Peer": {
        "nodekey:aaa": {"HostName": "phone"},
        "nodekey:bbb": {"HostName": "laptop"},
    },
})


def test_status_connected(monkeypatch):
    monkeypatch.setattr(ts.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(ts, "_run", lambda *a, **k: _cp(out=STATUS_JSON))
    s = ts.status()
    assert s == {
        "installed": True, "running": True, "state": "Running",
        "logged_in": True, "self_ip": "100.101.102.103",
        "tailnet": "example.com", "hostname": "0042-nucleus", "peers": 2,
    }


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(ts.shutil, "which", lambda _: None)
    s = ts.status()
    assert s["installed"] is False and s["running"] is False
    assert s["state"] == "NotInstalled"


def test_status_needs_login(monkeypatch):
    monkeypatch.setattr(ts.shutil, "which", lambda _: "/usr/bin/tailscale")
    j = json.dumps({"BackendState": "NeedsLogin", "Self": {}, "Peer": {}})
    monkeypatch.setattr(ts, "_run", lambda *a, **k: _cp(out=j))
    s = ts.status()
    assert s["running"] is False and s["logged_in"] is False
    assert s["state"] == "NeedsLogin" and s["self_ip"] == ""


def test_up_with_authkey(monkeypatch):
    seen = {}

    def fake(args, **k):
        seen["args"] = args
        return _cp(rc=0)

    monkeypatch.setattr(ts.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(ts, "_run", fake)
    r = ts.up(authkey="tskey-abc")
    assert r == {"connected": True, "auth_url": ""}
    assert "--authkey=tskey-abc" in seen["args"] and "up" in seen["args"]


def test_up_interactive_returns_auth_url(monkeypatch):
    url = "https://login.tailscale.com/a/1234deadbeef"
    out = f"\nTo authenticate, visit:\n\n\t{url}\n\n"
    monkeypatch.setattr(ts.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(ts, "_run", lambda *a, **k: _cp(rc=1, out=out))
    r = ts.up()
    assert r == {"connected": False, "auth_url": url}


def test_up_failure_raises(monkeypatch):
    monkeypatch.setattr(ts.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(ts, "_run", lambda *a, **k: _cp(rc=1, err="boom"))
    with pytest.raises(ts.TailscaleError):
        ts.up(authkey="bad")


def test_accounts_parses_switch_list(monkeypatch):
    out = (
        "ID     Tailnet          Account\n"
        "abc123 example.com      alice@example.com*\n"
        "def456 other.org        bob@other.org\n"
    )
    monkeypatch.setattr(ts.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(ts, "_run", lambda *a, **k: _cp(out=out))
    accts = ts.accounts()
    assert accts == [
        {"id": "abc123", "tailnet": "example.com", "account": "alice@example.com", "active": True},
        {"id": "def456", "tailnet": "other.org", "account": "bob@other.org", "active": False},
    ]


def test_switch_builds_command(monkeypatch):
    seen = {}

    def fake(args, **k):
        seen["args"] = args
        return _cp(rc=0)

    monkeypatch.setattr(ts.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(ts, "_run", fake)
    r = ts.switch("bob@other.org")
    assert r == {"switched": "bob@other.org"}
    assert seen["args"] == ["switch", "bob@other.org"]


def test_switch_requires_account(monkeypatch):
    monkeypatch.setattr(ts.shutil, "which", lambda _: "/usr/bin/tailscale")
    with pytest.raises(ts.TailscaleError):
        ts.switch("")


def test_run_missing_binary_raises(monkeypatch):
    monkeypatch.setattr(ts.shutil, "which", lambda _: None)
    with pytest.raises(ts.TailscaleError):
        ts._run(["status"])
