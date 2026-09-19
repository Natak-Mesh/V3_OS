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
def _write_cfg(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_load_config_voice_section(tmp_path, monkeypatch):
    cfg_path = _write_cfg(tmp_path, """
        node:
          id: 42
        mesh:
          subnet_prefix: 10.20.1
          mesh_802_ttl: 8
        voice:
          channel: 2
          channels: "1:Command,2:Squad"
          jitter_ms: 60
          lora_enabled: true
          stt_engine: vosk
    """)
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
        mesh:
          subnet_prefix: 10.20.1
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
