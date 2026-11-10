"""nucleusd.linktest + /api/v1/linktest — on-demand link tests. Off-box.

Parsers run on real tool output (tests/linktest_fixtures.py). Subprocess and
peer-HTTP calls are stubbed; nothing here starts iperf3 or touches a radio.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient

from nucleusd import linktest as lt
from nucleusd import linktest_router
from nucleusd.api import app
from nucleusd.schema import NucleusConfig
from tests.linktest_fixtures import (
    IPERF_RECEIVE, IPERF_REFUSED, IPERF_SEND, PING_DEAD, PING_NO_DEV_STDERR, PING_OK,
    PROC_NET_TCP, RNPROBE_NOPATH, RNPROBE_OK, RNPROBE_RNODE,
)

CFG = NucleusConfig.model_validate({
    "node": {"id": 42}, "mesh": {"password": "52235223"}, "ap": {"password": "52235223"},
})


def _done(stdout="", stderr="", rc=0):
    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)


# ── addressing ───────────────────────────────────────────────────

def test_addresses_are_link_subnet_plus_node_id():
    assert lt.local_addr(CFG, "wifi") == ("wlan1", "10.20.1.42")
    assert lt.local_addr(CFG, "halow") == ("halow0", "10.30.1.42")
    assert lt.peer_addr(CFG, "wifi", 47) == "10.20.1.47"
    assert lt.peer_addr(CFG, "halow", 47) == "10.30.1.47"


def test_peer_cannot_be_self():
    with pytest.raises(lt.LinkTestBadRequest):
        lt.peer_addr(CFG, "wifi", 42)


def test_server_callers_limited_to_mesh_subnets():
    assert lt.caller_allowed(CFG, "10.20.1.47")
    assert lt.caller_allowed(CFG, "10.30.1.47")
    assert lt.caller_allowed(CFG, "127.0.0.1")
    for bad in ("10.20.42.100", "10.0.0.5", "100.64.1.1", "testclient", ""):
        assert not lt.caller_allowed(CFG, bad)


# ── ping ─────────────────────────────────────────────────────────

def test_parse_ping_ok():
    r = lt.parse_ping(PING_OK)
    assert (r["sent"], r["received"], r["loss_pct"]) == (3, 3, 0.0)
    assert r["rtt_ms"] == {"min": 0.622, "avg": 1.056, "max": 1.755, "mdev": 0.498}


def test_parse_ping_total_loss_has_no_rtt():
    r = lt.parse_ping(PING_DEAD)
    assert (r["received"], r["loss_pct"], r["rtt_ms"]) == (0, 100.0, None)


def test_ping_binds_link_device(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=PING_OK, stderr="")
    monkeypatch.setattr(lt.subprocess, "run", run)
    r = lt.ping(CFG, "halow", 47, count=3)
    assert seen["cmd"][seen["cmd"].index("-I") + 1] == "halow0"
    assert seen["cmd"][-1] == "10.30.1.47"
    assert r["peer_ip"] == "10.30.1.47" and r["received"] == 3


def test_ping_missing_device_is_error(monkeypatch):
    monkeypatch.setattr(lt.subprocess, "run", _done(stderr=PING_NO_DEV_STDERR, rc=2))
    with pytest.raises(lt.LinkTestError, match="No such device"):
        lt.ping(CFG, "halow", 47)


# ── iperf3 ───────────────────────────────────────────────────────

def test_parse_iperf_send_reports_receiver_goodput():
    r = lt.parse_iperf(IPERF_SEND)
    assert r["direction"] == "send"
    assert r["mbps"] == 22.09 and r["mbps_sender"] == 22.43
    assert r["bytes"] == 13893632 and r["retransmits"] == 12


def test_parse_iperf_reverse_is_receive():
    r = lt.parse_iperf(IPERF_RECEIVE)
    assert r["direction"] == "receive" and r["mbps"] == 3.98


def test_parse_iperf_error_and_garbage():
    with pytest.raises(lt.LinkTestError, match="Connection refused"):
        lt.parse_iperf(IPERF_REFUSED)
    with pytest.raises(lt.LinkTestError, match="no JSON"):
        lt.parse_iperf("")


def test_listening_in_proc_net_tcp():
    assert lt.listening_in(PROC_NET_TCP, "10.20.1.42", 5201)
    assert not lt.listening_in(PROC_NET_TCP, "10.30.1.42", 5201)
    # 4242 is listening on br-lan (10.20.42.1), not on wlan1.
    assert lt.listening_in(PROC_NET_TCP, "10.20.42.1", 4242)
    assert not lt.listening_in(PROC_NET_TCP, "10.20.1.42", 4242)


def test_iperf_client_is_bound_to_link_and_asks_peer_first(monkeypatch):
    calls = []
    monkeypatch.setattr(lt, "_peer_start_server",
                        lambda ip, link, secs: calls.append(("peer", ip, link, secs)))

    def run(cmd, **kw):
        calls.append(("run", cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=IPERF_RECEIVE, stderr="")
    monkeypatch.setattr(lt.subprocess, "run", run)
    r = lt.iperf(CFG, "wifi", 47, secs=5, reverse=True)
    assert calls[0] == ("peer", "10.20.1.47", "wifi", 5)
    cmd = calls[1][1]
    assert cmd[cmd.index("-c") + 1] == "10.20.1.47"
    assert cmd[cmd.index("-B") + 1] == "10.20.1.42%wlan1"
    assert "-R" in cmd and "-J" in cmd
    assert r["direction"] == "receive" and r["dev"] == "wlan1"
    assert not lt._test_lock.locked()


def test_iperf_rejects_concurrent_test():
    assert lt._test_lock.acquire(blocking=False)
    try:
        with pytest.raises(lt.LinkTestBusy):
            lt.iperf(CFG, "wifi", 47)
    finally:
        lt._test_lock.release()


class _Proc:
    def __init__(self, alive=True):
        self.alive, self.terminated = alive, False

    def poll(self):
        return None if self.alive else 1

    def wait(self):
        return 0

    def terminate(self):
        self.terminated = True


def test_start_server_is_one_shot_and_time_bounded(monkeypatch):
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"] = cmd
        return _Proc()
    monkeypatch.setattr(lt.subprocess, "Popen", popen)
    monkeypatch.setattr(lt, "_server_proc", None)
    monkeypatch.setattr(lt, "_listening", lambda ip, port: ip == "10.30.1.42")
    r = lt.start_server(CFG, "halow", 10)
    assert seen["cmd"][:2] == ["timeout", str(10 + lt.SERVER_GRACE_S)]
    assert seen["cmd"][2:] == ["iperf3", "-s", "-1", "-B", "10.30.1.42", "-p", "5201"]
    assert r["ip"] == "10.30.1.42"
    # A second request while that server is alive is refused.
    with pytest.raises(lt.LinkTestBusy):
        lt.start_server(CFG, "halow", 10)


def test_start_server_reports_failure(monkeypatch):
    proc = _Proc(alive=False)
    monkeypatch.setattr(lt.subprocess, "Popen", lambda cmd, **kw: proc)
    monkeypatch.setattr(lt, "_server_proc", None)
    monkeypatch.setattr(lt, "_listening", lambda ip, port: False)
    with pytest.raises(lt.LinkTestError, match="did not start"):
        lt.start_server(CFG, "wifi", 5)


# ── rnprobe ──────────────────────────────────────────────────────

def test_parse_rnprobe_ok():
    r = lt.parse_rnprobe(RNPROBE_OK)
    assert (r["sent"], r["received"], r["loss_pct"]) == (2, 2, 0.0)
    assert r["rtt_ms"] == [10.353, 8.211] and r["rtt_avg_ms"] == 9.282
    assert r["hops"] == 1
    assert r["interface"] == "AutoInterfacePeer[eth0/fe80::8aa2:9eff:fee1:c8f2]"
    assert r["error"] is None and r["rssi_dbm"] is None


def test_parse_rnprobe_rnode_rf_stats_and_loss():
    r = lt.parse_rnprobe(RNPROBE_RNODE)
    assert (r["sent"], r["received"], r["loss_pct"]) == (2, 1, 50.0)
    assert r["rtt_ms"] == [1874.0]
    assert r["interface"] == "RNodeInterface[RNode LoRa]"
    assert (r["rssi_dbm"], r["snr_db"], r["quality_pct"]) == (-97.0, 6.25, 82.0)


def test_parse_rnprobe_no_path():
    r = lt.parse_rnprobe(RNPROBE_NOPATH)
    assert r["sent"] == 0 and "no path" in r["error"]


def test_rnprobe_runs_as_rnsd_user(monkeypatch):
    seen = {}
    monkeypatch.setattr(lt.reti, "transport", lambda: {})

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=RNPROBE_OK, stderr="")
    monkeypatch.setattr(lt.subprocess, "run", run)
    r = lt.rnprobe("FA65075C2A28F00976D5542E7C87EBFA", probes=2)
    cmd = seen["cmd"]
    assert cmd[:4] == ["runuser", "-u", "natak", "--"]
    assert cmd[cmd.index("--config") + 1] == "/home/natak/.reticulum"
    assert cmd[-2:] == ["lxmf.delivery", "fa65075c2a28f00976d5542e7c87ebfa"]
    assert r["received"] == 2


def test_rnprobe_refused_without_rnsd(monkeypatch):
    def down():
        raise lt.reti.ReticulumError("transport identity unreadable")
    monkeypatch.setattr(lt.reti, "transport", down)
    with pytest.raises(lt.LinkTestError, match="rnsd is not running"):
        lt.rnprobe("fa65075c2a28f00976d5542e7c87ebfa")


# ── HTTP layer ───────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(linktest_router.cfgio, "load", lambda: CFG)
    return TestClient(app)


def test_api_validation(client):
    post = client.post
    assert post("/api/v1/linktest/ping", json={"link": "eth0", "peer_id": 47}).status_code == 422
    assert post("/api/v1/linktest/iperf",
                json={"link": "wifi", "peer_id": 47, "secs": 999}).status_code == 422
    assert post("/api/v1/linktest/rnprobe", json={"dest": "xyz"}).status_code == 422
    assert post("/api/v1/linktest/ping", json={"link": "wifi", "peer_id": 42}).status_code == 400


def test_api_iperf_server_refuses_non_mesh_caller(client):
    # TestClient's peer address is "testclient" — not a mesh IP.
    r = client.post("/api/v1/linktest/iperf-server", json={"link": "wifi", "secs": 5})
    assert r.status_code == 403


def test_api_busy_is_409_and_peer_error_503(client, monkeypatch):
    def busy(*a):
        raise lt.LinkTestBusy("a link test is already running on this node")
    monkeypatch.setattr(lt, "iperf", busy)
    r = client.post("/api/v1/linktest/iperf", json={"link": "wifi", "peer_id": 47})
    assert r.status_code == 409

    def unreachable(*a):
        raise lt.LinkTestError("peer 10.20.1.47 unreachable: timed out")
    monkeypatch.setattr(lt, "iperf", unreachable)
    r = client.post("/api/v1/linktest/iperf", json={"link": "wifi", "peer_id": 47})
    assert r.status_code == 503 and "unreachable" in r.json()["detail"]


def test_old_peer_without_linktest_is_reported(monkeypatch):
    """Regression: a pre-linktest peer answers the POST with 405 (static mount),
    which surfaced as 'Method Not Allowed' instead of 'update it'."""
    import io
    import urllib.error

    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 405, "Method Not Allowed", {},
                                     io.BytesIO(b'{"detail":"Method Not Allowed"}'))
    monkeypatch.setattr(lt.urllib.request, "urlopen", urlopen)
    with pytest.raises(lt.LinkTestError, match="does not support link tests"):
        lt._peer_start_server("10.20.1.47", "wifi", 5)
