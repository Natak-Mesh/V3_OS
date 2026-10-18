"""Regression tests for the voice daemon's config parsing (off-box).

The voice daemon is a lift-and-shift; these lock down the two pure functions the
TUI voice page and channel control depend on:

  * load_config()   — reads /etc/nucleus/config.yaml `voice:` section into the
                      flat VOICE_* dict the daemon consumes (+ derived MESH_*).
  * parse_channels() — turns VOICE_CHANNELS="1:Command,2:Squad" into the ordered
                      (n, label) list the CHANNELS control reply / UI selector use.

Both run without ALSA/hidraw/hardware, so they are safe in CI.
"""

import textwrap

import pytest

from nucleusd.voice import daemon


# ── parse_channels ─────────────────────────────────────────────
def test_parse_channels_basic():
    assert daemon.parse_channels("1:Command,2:Squad", 1) == [
        (1, "Command"), (2, "Squad")]


def test_parse_channels_default_label_and_whitespace():
    assert daemon.parse_channels(" 3 , 4:Fires ", 1) == [
        (3, "Channel 3"), (4, "Fires")]


def test_parse_channels_drops_dupes_and_out_of_range():
    # 1 repeats (second dropped); 0 and 255 are out of the 1-254 range.
    assert daemon.parse_channels("1:A,1:B,0:X,255:Y,2:C", 1) == [
        (1, "A"), (2, "C")]


def test_parse_channels_empty_falls_back_to_default():
    assert daemon.parse_channels("", 7) == [(7, "Channel 7")]
    assert daemon.parse_channels("nonsense", 7) == [(7, "Channel 7")]


# ── load_config ────────────────────────────────────────────────
# load_config() goes through the validated schema (nucleusd.config.load), so the
# fixtures must be complete, valid configs: mesh.password + ap.password are
# required fields. _write_cfg injects them unless the body already sets them.
_REQUIRED = """\
mesh:
  password: "52235223"
ap:
  password: "52235223"
"""


def _write_cfg(tmp_path, body, *, add_required=True):
    p = tmp_path / "config.yaml"
    text = textwrap.dedent(body)
    if add_required:
        text = _REQUIRED + text
    p.write_text(text)
    return str(p)


def test_load_config_voice_section(tmp_path, monkeypatch):
    cfg_path = _write_cfg(tmp_path, """
        node:
          id: 42
        mesh:
          subnet_prefix: 10.20.1
          mesh_802_ttl: 8
          password: "52235223"
        voice:
          channel: 2
          channels: "1:Command,2:Squad"
          jitter_ms: 60
          lora_enabled: true
          stt_engine: vosk
    """, add_required=False)
    # ap.password still required; the body above overrides the mesh block only.
    with open(cfg_path, "a") as f:
        f.write('ap:\n  password: "52235223"\n')
    monkeypatch.setattr(daemon, "CONFIG_YAML", cfg_path)
    cfg = daemon.load_config()
    assert cfg["MESH_IP"] == "10.20.1.42"
    assert cfg["MESH_802_TTL"] == "8"
    assert cfg["VOICE_CHANNEL"] == "2"
    assert cfg["VOICE_CHANNELS"] == "1:Command,2:Squad"
    assert cfg["VOICE_JITTER_MS"] == "60"
    assert cfg["VOICE_LORA_ENABLED"] == "true"
    # And the channel list the UI selector renders from is well-formed.
    channels = daemon.parse_channels(cfg["VOICE_CHANNELS"],
                                     int(cfg["VOICE_CHANNEL"]))
    assert channels == [(1, "Command"), (2, "Squad")]


def test_load_config_defaults_when_voice_missing(tmp_path, monkeypatch):
    cfg_path = _write_cfg(tmp_path, """
        node:
          id: 7
    """)
    monkeypatch.setattr(daemon, "CONFIG_YAML", cfg_path)
    cfg = daemon.load_config()
    assert cfg["MESH_IP"] == "10.20.1.7"
    assert cfg["VOICE_CHANNEL"] == "1"
    assert cfg["VOICE_CHANNELS"] == "1:Command"
    assert cfg["VOICE_LORA_ENABLED"] == "false"


def test_load_config_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "CONFIG_YAML", str(tmp_path / "nope.yaml"))
    assert daemon.load_config() == {}


def test_load_config_invalid_config_is_empty(tmp_path, monkeypatch):
    # Regression: an invalid config (mesh.password too short) must fail the
    # schema and fall back to {} rather than feeding unvalidated values through.
    cfg_path = _write_cfg(tmp_path, """
        node:
          id: 42
        mesh:
          password: "short"
    """, add_required=False)
    with open(cfg_path, "a") as f:
        f.write('ap:\n  password: "52235223"\n')
    monkeypatch.setattr(daemon, "CONFIG_YAML", cfg_path)
    assert daemon.load_config() == {}


# ── tx_cleanup config (live TX mic conditioning) ───────────────
def test_load_config_tx_cleanup_defaults_on(tmp_path, monkeypatch):
    # Absent from the file -> the daemon-side default is on (matches schema).
    cfg_path = _write_cfg(tmp_path, """
        node:
          id: 42
        voice:
          channel: 1
    """)
    monkeypatch.setattr(daemon, "CONFIG_YAML", cfg_path)
    assert daemon.load_config()["VOICE_TX_CLEANUP"] == "true"


def test_load_config_tx_cleanup_can_be_disabled(tmp_path, monkeypatch):
    cfg_path = _write_cfg(tmp_path, """
        node:
          id: 42
        voice:
          channel: 1
          tx_cleanup: false
    """)
    monkeypatch.setattr(daemon, "CONFIG_YAML", cfg_path)
    assert daemon.load_config()["VOICE_TX_CLEANUP"] == "false"


def test_schema_tx_cleanup_default_true():
    from nucleusd.schema import VoiceConfig
    assert VoiceConfig().tx_cleanup is True


# ── HaLow: multi-iface mesh list + per-source seq dedupe ───────────────────
def test_load_config_mesh_ifaces_includes_halow(tmp_path, monkeypatch):
    cfg_path = _write_cfg(tmp_path, """
        node:
          id: 42
        halow:
          enabled: true
        voice:
          channel: 1
    """)
    monkeypatch.setattr(daemon, "CONFIG_YAML", cfg_path)
    cfg = daemon.load_config()
    assert cfg["MESH_IFACES"] == "wlan1:10.20.1.42,halow0:10.30.1.42"


def test_load_config_mesh_ifaces_wlan1_only_when_halow_off(tmp_path, monkeypatch):
    cfg_path = _write_cfg(tmp_path, """
        node:
          id: 42
        halow:
          enabled: false
        voice:
          channel: 1
    """)
    monkeypatch.setattr(daemon, "CONFIG_YAML", cfg_path)
    assert daemon.load_config()["MESH_IFACES"] == "wlan1:10.20.1.42"


def _bare_daemon():
    """A VoiceDaemon instance with just the seq-dedupe state wired (no __init__)."""
    import threading
    d = daemon.VoiceDaemon.__new__(daemon.VoiceDaemon)
    d._rx_last_seq = {}
    d._rx_seq_lock = threading.Lock()
    return d


def test_seq_duplicate_dropped_from_same_node():
    d = _bare_daemon()
    # First seq from node 7 is new; the same seq (second-radio copy) is a dup.
    assert d._seq_is_duplicate(7, 100) is False
    assert d._seq_is_duplicate(7, 100) is True
    # A new, higher seq advances the watermark.
    assert d._seq_is_duplicate(7, 101) is False
    # An older seq (already played) is dropped.
    assert d._seq_is_duplicate(7, 101) is True


def test_seq_tracked_per_node():
    d = _bare_daemon()
    assert d._seq_is_duplicate(1, 5) is False
    # A different node's identical seq is independent (not a dup).
    assert d._seq_is_duplicate(2, 5) is False


def test_seq_large_backward_jump_is_sender_restart():
    d = _bare_daemon()
    assert d._seq_is_duplicate(7, 5_000_000) is False
    # Sender rebooted and reset tx_seq to 0: a huge backward jump is accepted.
    assert d._seq_is_duplicate(7, 0) is False


# ── MicCleanup (shared HPF + WebRTC NS mic conditioner) ────────
def _tone_frame(amp=8000):
    """One 640-byte S16_LE 16 kHz 20 ms frame (FRAME_SAMPLES samples)."""
    import math as _m
    import struct as _s
    samples = [int(amp * _m.sin(2.0 * _m.pi * 440.0 * n / daemon.RATE))
               for n in range(daemon.FRAME_SAMPLES)]
    return _s.pack("<{}h".format(daemon.FRAME_SAMPLES), *samples)


def test_miccleanup_preserves_frame_format():
    frame = _tone_frame()
    assert len(frame) == daemon.FRAME_BYTES
    mc = daemon.MicCleanup("TX")
    out = mc.process(frame)
    # Same wire format out: exactly one 640-byte S16_LE frame.
    assert isinstance(out, (bytes, bytearray))
    assert len(out) == daemon.FRAME_BYTES


def test_miccleanup_hpf_only_fallback_when_ns_absent(monkeypatch):
    # Force the WebRTC NS import to fail -> HPF-only path, still valid frames.
    import builtins
    real_import = builtins.__import__

    def _no_ns(name, *a, **k):
        if name == "webrtc_noise_gain":
            raise ImportError("simulated missing webrtc-noise-gain")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_ns)
    mc = daemon.MicCleanup("TX")
    assert mc._ns is None
    out = mc.process(_tone_frame())
    assert len(out) == daemon.FRAME_BYTES


def test_miccleanup_reset_clears_filter_state():
    mc = daemon.MicCleanup("TX")
    mc.process(_tone_frame())
    mc.reset()
    assert mc.x1 == mc.x2 == mc.y1 == mc.y2 == 0.0


def test_miccleanup_highpass_attenuates_dc():
    # A constant (DC) frame must be strongly attenuated by the high-pass.
    import struct as _s
    dc = _s.pack("<{}h".format(daemon.FRAME_SAMPLES),
                 *([12000] * daemon.FRAME_SAMPLES))
    mc = daemon.MicCleanup("TX")
    mc._ns = None  # isolate the HPF stage from NS
    out = _s.unpack("<{}h".format(daemon.FRAME_SAMPLES), mc.process(dc))
    # Tail of the frame (filter settled) should be near zero, not ~12000.
    assert abs(out[-1]) < 1000
