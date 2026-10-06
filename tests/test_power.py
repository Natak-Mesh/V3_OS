"""nucleusd.power + /api/v1/power routes — regression tests.

The POWER page takes the node down, so the exact command issued matters: it must
be `systemctl reboot|poweroff` (nucleusd runs as root — no sudo). These tests pin
the command, the dict contract, and that the API routes + web UI wiring exist. No
real power action is ever taken — subprocess.run is monkeypatched.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from nucleusd import power
from nucleusd.api import app

client = TestClient(app)


class _FakeCompleted:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def test_reboot_issues_exact_command(monkeypatch):
    calls = []
    monkeypatch.setattr(power.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _FakeCompleted())
    assert power.reboot() == {"started": True}
    assert calls == [["systemctl", "reboot"]]


def test_poweroff_issues_exact_command(monkeypatch):
    calls = []
    monkeypatch.setattr(power.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _FakeCompleted())
    assert power.poweroff() == {"started": True}
    assert calls == [["systemctl", "poweroff"]]


def test_failure_reports_stderr(monkeypatch):
    monkeypatch.setattr(power.subprocess, "run",
                        lambda cmd, **kw: _FakeCompleted(returncode=1, stderr="a password is required\n"))
    r = power.reboot()
    assert r["started"] is False
    assert r["detail"] == "a password is required"


def test_power_routes_exist(monkeypatch):
    # Patch at the module the API calls into, so no real systemctl runs.
    import nucleusd.api as api
    monkeypatch.setattr(api.powermod, "reboot", lambda: {"started": True})
    monkeypatch.setattr(api.powermod, "poweroff", lambda: {"started": True})
    assert client.post("/api/v1/power/reboot").json() == {"started": True}
    assert client.post("/api/v1/power/poweroff").json() == {"started": True}


def test_power_page_wired_in_ui():
    web = Path(__file__).resolve().parent.parent / "nucleusd" / "web"
    app_js = (web / "app.js").read_text()
    assert 'to: "power"' in app_js, "POWER menu entry missing"
    assert "power: {" in app_js, "power page definition missing"
    assert "async function powerOp" in app_js, "powerOp handler missing"
    assert '/api/v1/power/${action}' in app_js, "powerOp no longer calls the API"
