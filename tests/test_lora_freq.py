"""Meshtastic LoRa frequency-slot math (nucleusd.meshtastic.lora_freq).

The Radio Configuration page shows the radio's real frequency (slot → band
edges in MHz) so an operator never has to work the slot number into a
frequency by hand, and so a sub-GHz setting can't silently land somewhere
unexpected. These pin the firmware-equivalent math off-box:

  * this node's real config (US / SHORT_FAST / "Natak") resolves to slot 69,
    919.000–919.250 MHz — matching meshtasticd's own log on 0042-nucleus;
  * the slot grid sizes follow region span / bandwidth (US 250 kHz → 104,
    US 125 kHz → 208, narrow EU_868 → 1);
  * the /freq API echoes the same descriptor and rejects bad input.
"""

from fastapi.testclient import TestClient

from nucleusd.api import app
from nucleusd.meshtastic import lora_freq as lf

client = TestClient(app)


def test_node_real_config_auto_slot_matches_radio():
    # US / SHORT_FAST (250 kHz) with channel name "Natak", slot 0 (auto).
    # meshtasticd on this node logs channel_num 69 at this preset/name.
    d = lf.describe("US", "SHORT_FAST", "Natak", 0)
    assert d["auto"] is True
    assert d["resolved_slot"] == 69
    assert d["low_mhz"] == 919.0
    assert d["high_mhz"] == 919.25
    assert d["num_slots"] == 104


def test_explicit_slot_band_edges():
    # Explicit slot bypasses the name hash: slot 20 at 250 kHz in US is
    # 902 + 0.125 + 19*0.25 = 906.875 MHz centre -> 906.75-907.0 MHz.
    d = lf.describe("US", "SHORT_FAST", "ignored", 20)
    assert d["auto"] is False
    assert d["resolved_slot"] == 20
    assert d["low_mhz"] == 906.75
    assert d["high_mhz"] == 907.0


def test_slot_count_follows_region_and_bandwidth():
    # 250 kHz presets: 26 MHz US span / 0.25 = 104 slots.
    assert lf.num_slots("SHORT_FAST", "US") == 104
    # 125 kHz presets double the grid -> 208 slots (the old fixed 104 cap
    # blocked the upper half).
    assert lf.num_slots("LONG_MODERATE", "US") == 208
    assert lf.num_slots("LONG_SLOW", "US") == 208
    # A narrow region fits a single slot.
    assert lf.num_slots("LONG_FAST", "EU_868") == 1


def test_lora24_uses_wide_bandwidth():
    # LORA_24 sets wide_lora: LONG_FAST widens 250 -> 812.5 kHz.
    assert lf.bandwidth_khz("LONG_FAST", "LORA_24") == 812.5
    assert lf.bandwidth_khz("LONG_FAST", "US") == 250.0


def test_slot_out_of_range_has_no_band():
    d = lf.describe("EU_868", "LONG_FAST", "x", 5)  # only 1 slot exists
    assert d["num_slots"] == 1
    assert d["low_mhz"] is None
    assert d["high_mhz"] is None


def test_freq_endpoint_echoes_descriptor():
    r = client.get("/api/v1/meshtastic/freq",
                   params={"region": "US", "preset": "SHORT_FAST",
                           "name": "Natak", "slot": 0})
    assert r.status_code == 200
    d = r.json()
    assert d["resolved_slot"] == 69
    assert d["low_mhz"] == 919.0
    assert d["high_mhz"] == 919.25


def test_freq_endpoint_rejects_bad_region_and_preset():
    assert client.get("/api/v1/meshtastic/freq",
                      params={"region": "ZZZ"}).status_code == 400
    assert client.get("/api/v1/meshtastic/freq",
                      params={"preset": "NOPE"}).status_code == 400
