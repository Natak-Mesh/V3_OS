"""nucleusd.modem73 + /api/v1/modem73 — live link tuning. Off-box.

Message handling runs on real control-port messages captured on node
0042 (tests/modem73_fixtures.py). The control port, amixer and apply are
stubbed; nothing here opens a socket to modem73 or keys a radio.
"""

import base64
import socket
import struct
import subprocess
import textwrap
import threading

import pytest
from fastapi.testclient import TestClient

from nucleusd import config as cfgio
from nucleusd.api import app
from nucleusd.modem73 import control as ctl
from nucleusd.modem73 import mixer as mx
from nucleusd.modem73 import router as rt
from nucleusd.schema import NucleusConfig
from nucleusd.modem73 import logtail as lt
from tests.modem73_fixtures import (
    CONFIG_CHANGED_RDM1200, GET_CONFIG_BPSK, GET_STATUS_LAST, LOG_CRC_FAIL,
    LOG_DECODED, RX_FRAME_EVENTS, RX_FRAME_HISTORY,
)

client = TestClient(app)
CFG = NucleusConfig.model_validate({
    "node": {"id": 42}, "mesh": {"password": "52235223"}, "ap": {"password": "52235223"},
})
# Captured frames, newest first (the order the monitor serves them).
FRAMES = [{k: v for k, v in f.items() if k != "age_s"} for f in RX_FRAME_HISTORY["frames"]]
NOW = FRAMES[0]["time"] + 5


def test_package_is_installed_by_pyproject():
    """Regression: pyproject lists packages explicitly; a missing entry means
    the deployed venv lacks nucleusd.modem73 and api.py's guarded import
    silently drops every /api/v1/modem73 route."""
    import tomllib
    from pathlib import Path
    py = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())
    assert "nucleusd.modem73" in py["tool"]["setuptools"]["packages"]


# ── wire protocol ────────────────────────────────────────────────

def test_encode_is_big_endian_length_prefixed_json():
    b = ctl.encode({"cmd": "get_status"})
    (n,) = struct.unpack(">I", b[:4])
    assert n == len(b) - 4
    assert b[4:] == b'{"cmd": "get_status"}'


def test_request_skips_pushed_events_before_reply(monkeypatch):
    """modem73 broadcasts events to every client, so an rx_frame can arrive
    between our command and its reply; request() must return the reply."""
    a, b = socket.socketpair()

    def serve():
        ctl.read_msg(b)  # the command
        b.sendall(ctl.encode(RX_FRAME_EVENTS[0]) + ctl.encode(GET_STATUS_LAST))

    t = threading.Thread(target=serve)
    t.start()
    monkeypatch.setattr(ctl.socket, "create_connection", lambda *a_, **k: a)
    assert ctl.request("get_status") == GET_STATUS_LAST
    t.join()
    b.close()


def test_request_unreachable_raises(monkeypatch):
    def refuse(*a, **k):
        raise ConnectionRefusedError(111, "Connection refused")
    monkeypatch.setattr(ctl.socket, "create_connection", refuse)
    with pytest.raises(ctl.Modem73Error):
        ctl.request("get_status")


def test_read_msg_rejects_oversize():
    a, b = socket.socketpair()
    b.sendall(struct.pack(">I", ctl.MAX_MSG + 1))
    with pytest.raises(ctl.Modem73Error):
        ctl.read_msg(a)
    a.close()
    b.close()


# ── monitor cache ────────────────────────────────────────────────

def test_monitor_caches_status_config_and_frames():
    m = ctl.Monitor()
    m.handle(GET_CONFIG_BPSK, "get_config")
    m.handle(RX_FRAME_HISTORY, "rx_frame_history")
    m.handle(GET_STATUS_LAST, "get_status")
    s = m.snapshot()
    assert s["config"]["modulation"] == "BPSK" and "ok" not in s["config"]
    assert s["status"]["rx_frame_count"] == 3 and "ok" not in s["status"]
    # Newest first, history's age_s stripped (it goes stale in a cache).
    assert [f["seq"] for f in s["frames"]] == [3, 2, 1]
    assert all("age_s" not in f for f in s["frames"])


def test_monitor_dedupes_event_and_history_copies():
    """An rx_frame event and the history catch-up after a reconnect carry the
    same frame; it must appear once."""
    m = ctl.Monitor()
    for ev in RX_FRAME_EVENTS:
        m.handle(ev, None)
    m.handle(RX_FRAME_HISTORY, "rx_frame_history")
    s = m.snapshot()
    assert [f["seq"] for f in s["frames"]] == [3, 2, 1]
    assert all("event" not in f for f in s["frames"])


def test_monitor_config_changed_event_updates_config():
    m = ctl.Monitor()
    m.handle(GET_CONFIG_BPSK, "get_config")
    v = m.snapshot()["version"]
    m.handle(CONFIG_CHANGED_RDM1200, None)
    s = m.snapshot()
    assert s["config"]["modulation"] == "RDM-1200"
    assert s["version"] > v


def test_monitor_ignores_unknown_and_failed_replies():
    m = ctl.Monitor()
    m.handle({"event": "something_new"}, None)
    m.handle({"ok": False, "error": "unknown command"}, "get_status")
    assert m.snapshot()["version"] == 0
    assert m.snapshot()["status"] is None


def test_monitor_keeps_last_max_frames():
    m = ctl.Monitor()
    base = RX_FRAME_EVENTS[0]
    for i in range(ctl.MAX_FRAMES + 10):
        m.handle({**base, "seq": i, "time": base["time"] + i}, None)
    frames = m.snapshot()["frames"]
    assert len(frames) == ctl.MAX_FRAMES
    assert frames[0]["seq"] == ctl.MAX_FRAMES + 9


# ── log tail (decoder detail the control port lacks) ─────────────

def _parse(text, now=100.0):
    p = lt.Parser()
    for ln in text.splitlines():
        p.feed(ln, now)
    return p


def test_log_decoded_frame_and_own_tx_block():
    p = _parse(LOG_DECODED)
    [a] = p.view()
    assert (a["callsign"], a["modulation"], a["code_rate"]) == ("N0CALL", "QPSK", "1/2")
    # Decoded: modem73's own SNR (what the TUI shows), not the per-symbol mean.
    assert a["ok"] is True and a["snr"] == 5.0 and a["ber_pct"] == pytest.approx(6.604)
    assert a["time"] == 100.0 and "syms" not in a
    # Our own BPSK TX block after it did not overwrite the RX attempt.


def test_log_crc_failures_are_attempts():
    """Regression: 0047 QAM256 3/4 frames reached 0042 but failed CRC; the
    control port reported nothing, so the LIVE page stayed empty."""
    v = _parse(LOG_CRC_FAIL).view()
    assert len(v) == 3                       # back-to-back frames start at SFO
    assert all(a["callsign"] == "0047" for a in v)
    assert [a["modulation"] for a in v] == [None, "QAM256", "QAM256"]
    assert [a["ok"] for a in v] == [None, False, False]   # newest is cut after callsign
    # No decoded SNR on a CRC failure: mean per-symbol SNR instead.
    assert v[2]["snr"] == 16.1 and v[2]["ber_pct"] is None


def test_logtail_reads_complete_lines_and_backfills(tmp_path):
    f = tmp_path / "modem73.log"
    f.write_text("junk\n" * 10)
    t = lt.LogTail(str(f))
    lines, backfill = t.read()
    assert backfill and lines == ["junk"] * 10
    with f.open("a") as fh:
        fh.write("Decoder: CRC failed\nDecoder: Sync")
    assert t.read() == (["Decoder: CRC failed"], False)    # partial line held back
    with f.open("a") as fh:
        fh.write(" found at sample 1\n")
    assert t.read() == (["Decoder: Sync found at sample 1"], False)
    f.write_text("x\n")                                       # truncated / rotated
    assert t.read() == (["x"], True)
    assert lt.LogTail(str(tmp_path / "missing")).read() == ([], False)


def test_monitor_poll_log_bumps_version(tmp_path):
    m = ctl.Monitor()
    m._tail = lt.LogTail(str(tmp_path / "modem73.log"))
    (tmp_path / "modem73.log").write_text(LOG_CRC_FAIL)
    m.poll_log()
    s = m.snapshot()
    assert s["version"] == 1 and len(s["attempts"]) == 3
    assert s["attempts"][0]["time"] is None                  # backfill: time unknown
    m.poll_log()
    assert m.snapshot()["version"] == 1


# ── mixer ────────────────────────────────────────────────────────

# Real `amixer -c Device cget name='Speaker Playback Volume'` output from 0042.
AMIXER_CGET_SPEAKER = """numid=6,iface=MIXER,name='Speaker Playback Volume'
  ; type=INTEGER,access=rw---R--,values=2,min=0,max=37,step=0
  : values=17,17
  | dBminmaxmute-min=-37.00dB,max=0.00dB
"""


def test_parse_cget_real_output():
    assert mx.parse_cget(AMIXER_CGET_SPEAKER) == 17
    assert mx.parse_cget("amixer: Cannot find the given element") is None


def test_mixer_set_failure_raises(monkeypatch):
    monkeypatch.setattr(mx.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr="amixer: Invalid card number."))
    with pytest.raises(mx.MixerError, match="Invalid card"):
        mx.set_value("Nope", mx.RX_GAIN, 10)


# ── router ───────────────────────────────────────────────────────

_CONFIG = """
    node:
      id: 42
    mesh:
      password: pw123456
    ap:
      password: ap123456
    reticulum:
      modem73_mode: bpsk_1_2
      modem73_callsign: N0CALL
      modem73_tx_level: 5
"""


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(_CONFIG))
    monkeypatch.setattr(cfgio, "CONFIG_PATH", p)
    return p


@pytest.fixture
def ctl_calls(monkeypatch):
    calls = []

    def fake(cmd, **params):
        calls.append((cmd, params))
        return {"ok": True}
    monkeypatch.setattr(ctl, "request", fake)
    return calls


def test_build_view_from_real_snapshot():
    m = ctl.Monitor()
    m.handle(GET_CONFIG_BPSK, "get_config")
    m.handle(RX_FRAME_HISTORY, "rx_frame_history")
    m.handle(GET_STATUS_LAST, "get_status")
    v = rt.build_view(CFG, m.snapshot(), {"rx_gain": 20, "tx_gain": 17}, now=NOW)
    assert v["callsign"] == "0042"
    # Live modem still on the dropped BPSK mode -> no registry key, raw modulation shown.
    assert v["live"]["mode"] is None and v["live"]["modulation"] == "BPSK"
    assert v["live"]["tx_level"] == 5          # tx_drive 0.05 -> 5 %
    assert v["saved"] == {"mode": "qpsk_1_2", "tx_level": 5, "rx_gain": 20, "tx_gain": 17}
    assert [f["seq"] for f in v["frames"]] == [3, 2, 1]
    assert "rdm_1200" in v["modes"]
    assert v["attempts"] == [] and "advice" not in v


def test_test_frame_sends_m73_text_frame(seeded, ctl_calls):
    r = client.post("/api/v1/modem73/test")
    assert r.status_code == 200
    cmd, params = ctl_calls[-1]
    assert cmd == "tx" and params["oper_mode"] == -1
    payload = base64.b64decode(params["data"])
    # modem73's native text format, with the node-id callsign (N0CALL = unset).
    assert payload.startswith(b"M73:0042:nucleus test ")
    assert len(payload) <= 500


def test_tx_level_maps_to_tx_drive(seeded, ctl_calls):
    assert client.put("/api/v1/modem73/tx_level", json={"tx_level": 12}).status_code == 200
    assert ctl_calls[-1] == ("set_config", {"tx_drive": 0.12})
    assert client.put("/api/v1/modem73/tx_level", json={"tx_level": 4}).status_code == 422


def test_control_port_down_is_503(seeded, monkeypatch):
    def down(cmd, **p):
        raise ctl.Modem73Error("modem73 control port unavailable: refused")
    monkeypatch.setattr(ctl, "request", down)
    assert client.post("/api/v1/modem73/test").status_code == 503


def test_modem_rejection_is_502(seeded, monkeypatch):
    monkeypatch.setattr(ctl, "request", lambda cmd, **p: {"ok": False})
    assert client.put("/api/v1/modem73/tx_level", json={"tx_level": 50}).status_code == 502


def test_mixer_sets_card_from_config(seeded, monkeypatch):
    sets = []
    monkeypatch.setattr(mx, "set_value", lambda card, name, v: sets.append((card, name, v)))
    monkeypatch.setattr(mx, "get", lambda card, name: 7)
    r = client.put("/api/v1/modem73/mixer", json={"rx_gain": 25})
    assert r.status_code == 200
    assert sets == [("Device", mx.RX_GAIN, 25)]
    assert client.put("/api/v1/modem73/mixer", json={"tx_gain": 38}).status_code == 422


def test_save_persists_and_applies(seeded, monkeypatch):
    applied = []

    class Result:
        changed = ["/opt/nucleus/bin/nucleus-modem73-up.sh"]
        units_restarted = ["nucleus-modem73"]
    monkeypatch.setattr(rt, "apply_config", lambda cfg: applied.append(cfg) or Result())
    r = client.post("/api/v1/modem73/save",
                    json={"mode": "rdm_1200", "tx_level": 8, "rx_gain": 22, "tx_gain": 15})
    assert r.status_code == 200
    assert r.json()["units_restarted"] == ["nucleus-modem73"]
    saved = cfgio.load()
    assert saved.reticulum.modem73_mode == "rdm_1200"
    assert (saved.reticulum.modem73_tx_level, saved.reticulum.modem73_rx_gain,
            saved.reticulum.modem73_tx_gain) == (8, 22, 15)
    # Old placeholder callsign normalized away -> node id on air.
    assert saved.reticulum.modem73_callsign is None
    assert applied and applied[0].reticulum.modem73_mode == "rdm_1200"


def test_save_rejects_unknown_mode(seeded, monkeypatch):
    monkeypatch.setattr(rt, "apply_config", lambda cfg: pytest.fail("must not apply"))
    assert client.post("/api/v1/modem73/save", json={"mode": "bpsk_1_2"}).status_code == 422
