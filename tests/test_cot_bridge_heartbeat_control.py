"""CoT bridge manual-heartbeat control tests. Run off-box: `pytest` from root.

The bridge owns the LoRa radio, so a web/CLI "send heartbeat now" request is
relayed to it over a localhost UDP control socket (one JSON request -> one JSON
reply). These tests drive _heartbeat_control_loop() with a fake socket and fake
radio interface to confirm:
  - a {"cmd":"heartbeat"} request triggers one radio send and replies ok,
  - a dead radio replies ok=false with the error (no exception escapes),
  - an unknown command replies ok=false without sending.

The bridge module imports `from pubsub import pub` at top level; that package
lives in the production venv (/opt/nucleus/venv) but not the dev .venv, so we
stub it in sys.modules before importing.
"""

import json
import sys
import types

import pytest

# ── Stub the production-only `pubsub` package so the module imports off-box ──
if "pubsub" not in sys.modules:
    _pubsub = types.ModuleType("pubsub")
    _pubsub.pub = types.SimpleNamespace(
        subscribe=lambda *a, **k: None,
        sendMessage=lambda *a, **k: None,
        AUTO_TOPIC=object(),
    )
    sys.modules["pubsub"] = _pubsub

from nucleusd.meshtastic import cot_bridge  # noqa: E402

HEARTBEAT_PORTNUM = cot_bridge.HEARTBEAT_PORTNUM
CLIENT_ADDR = ("127.0.0.1", 54321)


class _FakeControlSock:
    """One request in, capture the reply, then break the loop.

    recvfrom() returns the queued datagram once, then raises OSError so the
    loop's `except OSError: break` exits cleanly (as a real closed socket does).
    """

    def __init__(self, request_bytes):
        self._queue = [(request_bytes, CLIENT_ADDR)]
        self.sent = []

    def recvfrom(self, _bufsize):
        if self._queue:
            return self._queue.pop(0)
        raise OSError("closed")

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class _FakeIface:
    """Captures sendData calls; optionally fails to simulate a dead link."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def sendData(self, payload, portNum=None, wantAck=None):
        self.calls.append({"payload": payload, "portNum": portNum,
                           "wantAck": wantAck})
        if self.fail:
            raise OSError("meshtasticd link down")


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Deterministic clock + reset the shared heartbeat timer and reconnect flag."""
    monkeypatch.setattr(cot_bridge.time, "time", lambda: 1000.0)
    cot_bridge._last_heartbeat = 0.0
    cot_bridge._radio_disconnected.clear()
    # _build_heartbeat_payload reads owner names off iface; a bare v1 beacon is
    # a valid fallback, so stub it to a fixed payload to keep the test focused.
    monkeypatch.setattr(cot_bridge, "_build_heartbeat_payload",
                        lambda: cot_bridge.HEARTBEAT_V1)


def _reply(sock):
    assert len(sock.sent) == 1
    data, addr = sock.sent[0]
    assert addr == CLIENT_ADDR
    return json.loads(data.decode("utf-8"))


def test_heartbeat_command_sends_and_acks(monkeypatch):
    iface = _FakeIface()
    monkeypatch.setattr(cot_bridge, "iface", iface)
    sock = _FakeControlSock(json.dumps({"cmd": "heartbeat"}).encode("utf-8"))

    cot_bridge._heartbeat_control_loop(sock)

    # Exactly one heartbeat packet went to the radio on the right portnum.
    assert len(iface.calls) == 1
    assert iface.calls[0]["portNum"] == HEARTBEAT_PORTNUM
    assert iface.calls[0]["wantAck"] is False
    # Caller got an ok reply, and the shared periodic timer was reset.
    assert _reply(sock) == {"ok": True}
    assert cot_bridge._last_heartbeat == 1000.0


def test_heartbeat_command_reports_send_failure(monkeypatch):
    iface = _FakeIface(fail=True)
    monkeypatch.setattr(cot_bridge, "iface", iface)
    sock = _FakeControlSock(json.dumps({"cmd": "heartbeat"}).encode("utf-8"))

    cot_bridge._heartbeat_control_loop(sock)

    reply = _reply(sock)
    assert reply["ok"] is False
    assert "link down" in reply["error"]
    # A failed send flags the radio for reconnect and does not move the timer.
    assert cot_bridge._radio_disconnected.is_set()
    assert cot_bridge._last_heartbeat == 0.0


def test_unknown_command_is_rejected(monkeypatch):
    iface = _FakeIface()
    monkeypatch.setattr(cot_bridge, "iface", iface)
    sock = _FakeControlSock(json.dumps({"cmd": "nope"}).encode("utf-8"))

    cot_bridge._heartbeat_control_loop(sock)

    assert iface.calls == []            # nothing sent to the radio
    assert _reply(sock)["ok"] is False
