"""nucleusd.modem73 + /api/v1/modem73 — live link tuning. Off-box.

Message handling and advice run on real control-port messages captured on node
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
from nucleusd.modem73 import advice as adv
from nucleusd.modem73 import control as ctl
from nucleusd.modem73 import mixer as mx
from nucleusd.modem73 import router as rt
from nucleusd.schema import NucleusConfig
from tests.modem73_fixtures import (
    CONFIG_CHANGED_RDM1200, GET_CONFIG_BPSK, GET_STATUS_LAST,
    RX_FRAME_EVENTS, RX_FRAME_HISTORY, RX_FRAME_NOISE,
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


# ── advice ───────────────────────────────────────────────────────

def test_advice_real_overdriven_link_blames_far_tx():
    """Captured 0047 -> 0042 link: 0047 at tx-level 100, level fine (-12..-17
    dBFS), SNR ~4.9 dB, BER ~6.5% -> far-end TX overdrive, not RX level."""
    a = adv.advise(FRAMES, GET_STATUS_LAST, now=NOW)
    assert a["sender"] == "N0CALL"
    assert a["level"] == "ok"
    texts = " ".join(i["text"] for i in a["items"])
    assert "overdriven" in texts and "Lower ITS TX level" in texts
    assert a["items"][0]["sev"] == "bad"


def test_advice_level_problems_take_priority():
    hot = [{**FRAMES[0], "level_db": -2.0}]
    a = adv.advise(hot, GET_STATUS_LAST, now=NOW)
    assert a["level"] == "high"
    assert len(a["items"]) == 1 and "volume down" in a["items"][0]["text"]
    quiet = [{**FRAMES[0], "level_db": -55.0}]
    a = adv.advise(quiet, GET_STATUS_LAST, now=NOW)
    assert a["level"] == "low" and "volume" in a["items"][0]["text"]


def test_advice_ignores_noise_decodes():
    """Regression: a callsign-less MFSK 'decode' of hiss (level -88 dBFS) was
    the newest frame and produced 'RX level very low' advice."""
    noise = {k: v for k, v in RX_FRAME_NOISE.items() if k != "event"}
    a = adv.advise([noise] + FRAMES, GET_STATUS_LAST, now=NOW)
    assert a["sender"] == "N0CALL" and a["level"] == "ok"
    assert adv.advise([noise], GET_STATUS_LAST, now=NOW)["sender"] is None


def test_advice_clean_link_suggests_faster_mode():
    clean = [{**f, "snr": 22.0, "ber_pct": 0.1} for f in FRAMES]
    a = adv.advise(clean, {**GET_STATUS_LAST, "crc_errors": 0}, now=NOW)
    assert a["items"][-1]["sev"] == "ok"
    assert "qam16_1_2" in a["items"][-1]["text"]


def test_advice_marginal_link_suggests_hardier_mode():
    marginal = [{**f, "snr": 12.0, "ber_pct": 9.0} for f in FRAMES]
    a = adv.advise(marginal, GET_STATUS_LAST, now=NOW)
    assert any("rdm_1200" in i["text"] for i in a["items"])


def test_advice_stale_and_empty():
    a = adv.advise(FRAMES, GET_STATUS_LAST, now=NOW + 3600)
    assert "min old" in a["items"][0]["text"]
    assert adv.advise([], None)["items"][0]["sev"] == "warn"


def test_level_state_zero_means_unknown():
    # modem73 reports 0.0 when no recent audio block was available.
    assert adv.level_state(0.0) == "unknown"
    assert adv.level_state(-17.1) == "ok"


def test_frame_mode_key():
    assert adv.frame_mode_key(FRAMES[0]) == "qpsk_1_2"
    assert adv.frame_mode_key({"modem": "robust", "mode": "RDM-1200"}) == "rdm_1200"
    assert adv.frame_mode_key({"modem": "mfsk", "mode": "MFSK-16"}) is None


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
    assert v["advice"]["level"] == "ok"
    assert "rdm_1200" in v["modes"]


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
