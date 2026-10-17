#!/usr/bin/env python3
"""Meshtastic LoRa frequency math — slot → band edges (MHz).

Pure, hardware-free reimplementation of the frequency-slot calculation the
Meshtastic firmware performs in RadioInterface::applyModemConfig(). Given a
region, modem preset and channel name, this derives the number of slots, the
slot that "auto" (channel_num 0) resolves to, and the band edges (low–high
MHz) each slot occupies. The UI uses this to show the real frequency of the
configured slot without the operator doing the math.

Transcribed verbatim from meshtastic/firmware:
  * region table (freqStart/freqEnd/spacing/wideLora) — RadioInterface.cpp
  * preset → bandwidth (kHz)                          — MeshRadio.h
  * numChannels / channel_num / freq formulas         — RadioInterface.cpp
  * djb2 name hash                                    — RadioInterface.cpp

Kept intentionally separate from meshtastic_api.py (which shells out to the
CLI) so it stays trivially unit-testable off-box.
"""

from __future__ import annotations

import math

# Region band limits: name -> (freq_start_mhz, freq_end_mhz, spacing_mhz,
# wide_lora). spacing is 0 for every current region; kept for fidelity with
# the firmware formula. wide_lora widens the preset bandwidths (2.4 GHz only).
# Transcribed from RadioInterface.cpp `regions[]`.
REGIONS: dict[str, tuple[float, float, float, bool]] = {
    "US": (902.0, 928.0, 0.0, False),
    "EU_433": (433.0, 434.0, 0.0, False),
    "EU_868": (869.4, 869.65, 0.0, False),
    "CN": (470.0, 510.0, 0.0, False),
    "JP": (920.5, 923.5, 0.0, False),
    "ANZ": (915.0, 928.0, 0.0, False),
    "ANZ_433": (433.05, 434.79, 0.0, False),
    "RU": (868.7, 869.2, 0.0, False),
    "KR": (920.0, 923.0, 0.0, False),
    "TW": (920.0, 925.0, 0.0, False),
    "IN": (865.0, 867.0, 0.0, False),
    "NZ_865": (864.0, 868.0, 0.0, False),
    "TH": (920.0, 925.0, 0.0, False),
    "UA_433": (433.0, 434.7, 0.0, False),
    "UA_868": (868.0, 868.6, 0.0, False),
    "MY_433": (433.0, 435.0, 0.0, False),
    "MY_919": (919.0, 924.0, 0.0, False),
    "SG_923": (917.0, 925.0, 0.0, False),
    "PH_433": (433.0, 434.7, 0.0, False),
    "PH_868": (868.0, 869.4, 0.0, False),
    "PH_915": (915.0, 918.0, 0.0, False),
    "KZ_433": (433.075, 434.775, 0.0, False),
    "KZ_863": (863.0, 868.0, 0.0, False),
    "NP_865": (865.0, 868.0, 0.0, False),
    "BR_902": (902.0, 907.5, 0.0, False),
    "LORA_24": (2400.0, 2483.5, 0.0, True),
    "UNSET": (902.0, 928.0, 0.0, False),
}

# Modem preset -> (narrow_bw_khz, wide_bw_khz). The wide value applies when the
# region's wide_lora flag is set (LORA_24). Transcribed from MeshRadio.h
# modemPresetToParams().
PRESET_BW_KHZ: dict[str, tuple[float, float]] = {
    "SHORT_TURBO": (500.0, 1625.0),
    "SHORT_FAST": (250.0, 812.5),
    "SHORT_SLOW": (250.0, 812.5),
    "MEDIUM_FAST": (250.0, 812.5),
    "MEDIUM_SLOW": (250.0, 812.5),
    "LONG_FAST": (250.0, 812.5),
    "LONG_MODERATE": (125.0, 406.25),
    "LONG_SLOW": (125.0, 406.25),
    "LONG_TURBO": (500.0, 1625.0),
}


def _hash(name: str) -> int:
    """djb2 (Dan Bernstein), as RadioInterface::hash() — 32-bit unsigned."""
    h = 5381
    for ch in name.encode("utf-8", "replace"):
        h = ((h << 5) + h + ch) & 0xFFFFFFFF
    return h


def bandwidth_khz(preset: str, region: str = "US") -> float:
    """Channel bandwidth (kHz) for a preset in a region (wide for LORA_24)."""
    narrow, wide = PRESET_BW_KHZ[preset]
    _, _, _, wide_lora = REGIONS.get(region, REGIONS["UNSET"])
    return wide if wide_lora else narrow


def num_slots(preset: str, region: str = "US") -> int:
    """Number of frequency slots for a preset in a region.

    Mirrors the firmware:
        numChannels = floor((freqEnd - freqStart) / (spacing + bw_mhz))
    """
    freq_start, freq_end, spacing, _ = REGIONS.get(region, REGIONS["UNSET"])
    bw_mhz = bandwidth_khz(preset, region) / 1000.0
    denom = spacing + bw_mhz
    if denom <= 0:
        return 0
    return int(math.floor((freq_end - freq_start) / denom))


def auto_slot(preset: str, region: str, channel_name: str) -> int:
    """1-based slot that channel_num 0 (auto) resolves to for this name.

    The firmware hashes the channel name and takes it modulo numChannels; we
    return the 1-based slot number a user would set to pin that same choice.
    """
    n = num_slots(preset, region)
    if n <= 0:
        return 0
    return (_hash(channel_name or "") % n) + 1


def slot_band(slot: int, preset: str, region: str = "US") -> tuple[float, float]:
    """(low_mhz, high_mhz) band edges a 1-based slot occupies.

    Centre matches the firmware's
        freq = freqStart + bw/2000 + (channel_num * bw/1000)
    with channel_num = slot - 1 (0-based). Edges are centre ± bw/2.
    """
    freq_start, _, _, _ = REGIONS.get(region, REGIONS["UNSET"])
    bw_khz = bandwidth_khz(preset, region)
    ch = max(0, int(slot) - 1)
    centre = freq_start + (bw_khz / 2000.0) + (ch * bw_khz / 1000.0)
    half = bw_khz / 2000.0
    return (round(centre - half, 4), round(centre + half, 4))


def describe(region: str, preset: str, channel_name: str,
             slot: int = 0) -> dict:
    """Full frequency readout for a region/preset/name/slot selection.

    `slot` is the configured frequency_slot (0 = auto). Returns the effective
    1-based slot, its band edges, the bandwidth, the total slot count and the
    slot that auto resolves to — everything the UI needs to show the real
    frequency and let the operator pick another slot.
    """
    region = region if region in REGIONS else "UNSET"
    freq_start = REGIONS[region][0]
    n = num_slots(preset, region)
    resolved = int(slot) if slot else auto_slot(preset, region, channel_name)
    low = high = None
    if 1 <= resolved <= n:
        low, high = slot_band(resolved, preset, region)
    return {
        "region": region,
        "modem_preset": preset,
        "channel_name": channel_name or "",
        "bandwidth_khz": bandwidth_khz(preset, region),
        "freq_start_mhz": freq_start,
        "num_slots": n,
        "configured_slot": int(slot),
        "auto": not slot,
        "resolved_slot": resolved,
        "low_mhz": low,
        "high_mhz": high,
    }

