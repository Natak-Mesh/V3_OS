"""Link-quality advice from decoded-frame metrics. Pure; unit-tested.

Everything here describes what THIS node hears, i.e. the FAR end's
transmission: level is this node's RX audio (radio volume knob + sound-card
capture gain), while SNR/BER mostly reflect the far end's TX drive and mode.

Order matters: fix the receive level first (a clipped or starved input makes
SNR/BER meaningless), then far-end TX drive (OFDM overdrive is the common
fault: bench 0054->0042 went SNR 3.7 -> 12.7 dB going tx-level 100 -> 5),
then mode. Thresholds are first-cut values from bench data on UV-5R radios
(see docs/manual/reticulum.md) and should be refined with range testing.
"""

from __future__ import annotations

import time

from ..schema import MODEM73_MODES

# RX level (dBFS, sampled at frame end) bounds. Decodes were clean at -12..-17.
LEVEL_HIGH_DB = -6.0     # above: near full scale, clipping risk
LEVEL_LOW_DB = -40.0     # below: input starved
# Pre-FEC BER (%) / SNR (dB) bands.
BER_BAD_PCT = 8.0
BER_POOR_PCT = 3.0
BER_GOOD_PCT = 1.0
SNR_POOR_DB = 10.0
SNR_MARGIN_DB = 15.0
STALE_S = 300.0

# Registry order is robust -> fast; advice steps along it.
MODE_ORDER = list(MODEM73_MODES)


def level_state(level_db: float | None) -> str:
    """'high' | 'low' | 'ok' | 'unknown' for a frame's RX level."""
    # modem73 reports exactly 0.0 when no recent audio block was available.
    if level_db is None or level_db == 0.0:
        return "unknown"
    if level_db > LEVEL_HIGH_DB:
        return "high"
    if level_db < LEVEL_LOW_DB:
        return "low"
    return "ok"


def _step(mode_key: str | None, delta: int) -> str | None:
    if mode_key not in MODE_ORDER:
        return None
    i = MODE_ORDER.index(mode_key) + delta
    return MODE_ORDER[i] if 0 <= i < len(MODE_ORDER) else None


def frame_mode_key(frame: dict) -> str | None:
    """MODEM73_MODES key of a decoded frame's mode, or None if not in registry."""
    for key, m in MODEM73_MODES.items():
        if frame.get("modem") == "robust" and m["family"] == "robust" \
                and frame.get("mode") == m["modulation"]:
            return key
        if frame.get("modem") == "ofdm" and m["family"] == "ofdm" \
                and frame.get("modulation") == m["modulation"] \
                and frame.get("code_rate") == m["rate"]:
            return key
    return None


def advise(frames: list[dict], status: dict | None, now: float | None = None) -> dict:
    """Advice for the most recent sender. `frames` newest first.

    Returns {"sender", "level", "items": [{"sev": ok|warn|bad, "text"}]}.
    """
    now = time.time() if now is None else now
    # A real station frame carries its callsign (PHY header). Callsign-less
    # "decodes" are noise false-positives (seen live: MFSK-16 at SNR -10 dB),
    # so they never drive advice.
    frames = [f for f in frames if f.get("callsign")]
    if not frames:
        return {"sender": None, "level": "unknown", "items": [{
            "sev": "warn",
            "text": "No frames decoded yet. Have the far node press SEND TEST FRAME."}]}
    last = frames[0]
    sender = last.get("callsign")
    recent = [f for f in frames if f.get("callsign") == sender][:5]
    items: list[dict] = []
    age = now - (last.get("time") or now)
    if age > STALE_S:
        items.append({"sev": "warn", "text": f"Last frame is {int(age // 60)} min old; "
                                             "readings may not reflect the link now."})
    lvl = level_state(last.get("level_db"))
    if lvl == "high":
        items.append({"sev": "bad", "text": "RX level near full scale (clipping). Turn THIS "
                                            "radio's volume down, or lower RX gain."})
    elif lvl == "low":
        items.append({"sev": "bad", "text": "RX level very low. Turn THIS radio's volume "
                                            "up, or raise RX gain."})
    if lvl in ("high", "low"):
        return {"sender": sender, "level": lvl, "items": items}

    snr = sum(f.get("snr", 0.0) for f in recent) / len(recent)
    bers = [f["ber_pct"] for f in recent if (f.get("ber_pct") or -1) >= 0]
    ber = sum(bers) / len(bers) if bers else None
    crc = (status or {}).get("crc_errors", 0) or 0
    mode = frame_mode_key(last)

    if ber is not None and ber >= BER_POOR_PCT and snr < SNR_POOR_DB:
        items.append({"sev": "bad", "text": f"SNR {snr:.1f} dB / BER {ber:.1f}%: far node's "
                                            "TX is likely overdriven. Lower ITS TX level first."})
    if (ber is not None and ber >= BER_BAD_PCT) or crc > 0:
        down = _step(mode, -1)
        if down:
            items.append({"sev": "warn", "text": f"Marginal decode (BER/CRC errors). If TX level "
                                                 f"is already low, far node: try {down}."})
    elif ber is not None and ber < BER_GOOD_PCT and snr >= SNR_MARGIN_DB:
        up = _step(mode, +1)
        if up:
            items.append({"sev": "ok", "text": f"Good margin (SNR {snr:.1f} dB). Far node could "
                                               f"try faster mode {up}."})
    if not items:
        items.append({"sev": "ok", "text": "Link OK for the current mode."})
    return {"sender": sender, "level": lvl, "items": items}
