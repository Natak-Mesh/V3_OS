"""USB GPS -> flight controller bridge (MAVLink GPS_INPUT).

The FC has no GPS of its own. A u-blox receiver on the Pi's USB emits NMEA;
this daemon parses it and sends GPS_INPUT (msg 232) into mavlink-router's local
endpoint (127.0.0.1:14560), which forwards it to the FC on the UART. The FC
uses it as GPS1 (GPS1_TYPE=14 / MAV) and reports position to the GCS in its
normal telemetry.

Parsing and message building are pure functions (testable off-box); pyserial
and pymavlink are imported only by run().
"""

from __future__ import annotations

import argparse
import glob
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("nucleus-gps")

DEFAULT_DEVICE_GLOB = "/dev/serial/by-id/*u-blox*"
DEFAULT_URL = "udpout:127.0.0.1:14560"
SOURCE_SYSTEM = 1       # same system as the FC
SOURCE_COMPONENT = 220  # MAV_COMP_ID_GPS
DEFAULT_RATE_HZ = 5

# GPS time = UTC + leap seconds (18 since 2017-01-01).
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
GPS_LEAP_SECONDS = 18

# GPS_INPUT_IGNORE_FLAG_*
IGNORE_ALT = 1
IGNORE_HDOP = 2
IGNORE_VDOP = 4
IGNORE_VEL_HORIZ = 8
IGNORE_VEL_VERT = 16
IGNORE_SPEED_ACCURACY = 32
IGNORE_HORIZONTAL_ACCURACY = 64
IGNORE_VERTICAL_ACCURACY = 128

KNOTS_TO_MS = 0.514444


# ---------------------------------------------------------------- NMEA parsing

def nmea_fields(line: str) -> list[str] | None:
    """Validate an NMEA sentence's checksum; return its fields or None.

    fields[0] is the sentence type without talker (e.g. "GGA"), so GP/GN/GL
    talkers are handled identically.
    """
    # Start at "$": a UBX ACK (binary, no newline) can prefix the next line.
    start = line.find("$")
    if start < 0:
        return None
    line = line[start:].strip()
    if "*" not in line:
        return None
    body, _, cs = line[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        if int(cs[:2], 16) != calc:
            return None
    except ValueError:
        return None
    fields = body.split(",")
    if len(fields[0]) < 5:
        return None
    fields[0] = fields[0][2:]
    return fields


def _float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _latlon(value: str, hemi: str, deg_digits: int) -> float | None:
    """NMEA ddmm.mmmm / dddmm.mmmm + hemisphere -> signed decimal degrees."""
    if len(value) <= deg_digits:
        return None
    deg = _float(value[:deg_digits])
    minutes = _float(value[deg_digits:])
    if deg is None or minutes is None:
        return None
    out = deg + minutes / 60.0
    return -out if hemi in ("S", "W") else out


def parse_gga(f: list[str]) -> dict | None:
    if len(f) < 10:
        return None
    return {
        "time": f[1],
        "lat": _latlon(f[2], f[3], 2),
        "lon": _latlon(f[4], f[5], 3),
        "quality": int(f[6]) if f[6].isdigit() else 0,
        "sats": int(f[7]) if f[7].isdigit() else 0,
        "hdop": _float(f[8]),
        "alt": _float(f[9]),
    }


def parse_rmc(f: list[str]) -> dict | None:
    if len(f) < 10:
        return None
    return {
        "time": f[1],
        "valid": f[2] == "A",
        "speed_kn": _float(f[7]),
        "course": _float(f[8]),
        "date": f[9],
    }


def parse_gsa(f: list[str]) -> dict | None:
    if len(f) < 18:
        return None
    return {
        "mode": int(f[2]) if f[2].isdigit() else 1,
        "vdop": _float(f[17]),
    }


# ------------------------------------------------------------ message building

def utc_from_nmea(date: str, hms: str) -> datetime | None:
    """ddmmyy + hhmmss.ss -> aware UTC datetime."""
    if len(date) != 6 or len(hms) < 6:
        return None
    try:
        return datetime(
            2000 + int(date[4:6]), int(date[2:4]), int(date[0:2]),
            int(hms[0:2]), int(hms[2:4]), tzinfo=timezone.utc,
        ) + timedelta(seconds=float(hms[4:]))
    except ValueError:
        return None


def gps_week(utc: datetime) -> tuple[int, int]:
    """UTC datetime -> (GPS week, ms into week)."""
    total_ms = round(
        ((utc - GPS_EPOCH).total_seconds() + GPS_LEAP_SECONDS) * 1000)
    week_ms = 7 * 24 * 3600 * 1000
    return total_ms // week_ms, total_ms % week_ms


def fix_type(gga: dict, gsa: dict | None) -> int:
    """GGA quality + GSA mode -> MAVLink GPS_FIX_TYPE."""
    q = gga["quality"]
    if q == 0 or gga["lat"] is None or gga["lon"] is None:
        return 1  # NO_FIX
    if gsa is not None and gsa["mode"] == 2:
        return 2  # 2D
    if q == 4:
        return 6  # RTK_FIXED
    if q == 5:
        return 5  # RTK_FLOAT
    if q == 2:
        return 4  # DGPS
    return 3      # 3D


def build_gps_input(gga: dict, rmc: dict | None, gsa: dict | None,
                    now_us: int, gps_id: int = 0) -> dict:
    """Assemble GPS_INPUT kwargs (pymavlink gps_input_send) for one epoch.

    Triggered on GGA. RMC (velocity, date) is used only if it is from the same
    epoch (same UTC time); GSA (2D/3D, VDOP) is taken from the latest seen.
    """
    ignore = (IGNORE_VEL_VERT | IGNORE_SPEED_ACCURACY
              | IGNORE_HORIZONTAL_ACCURACY | IGNORE_VERTICAL_ACCURACY)
    ft = fix_type(gga, gsa)
    same_epoch = rmc is not None and rmc["time"] == gga["time"]

    week, week_ms = 0, 0
    if same_epoch:
        utc = utc_from_nmea(rmc["date"], rmc["time"])
        if utc is not None:
            week, week_ms = gps_week(utc)

    vn = ve = 0.0
    if same_epoch and rmc["valid"] and rmc["speed_kn"] is not None:
        speed = rmc["speed_kn"] * KNOTS_TO_MS
        course = math.radians(rmc["course"] or 0.0)
        vn, ve = speed * math.cos(course), speed * math.sin(course)
    else:
        ignore |= IGNORE_VEL_HORIZ

    alt, hdop = gga["alt"], gga["hdop"]
    vdop = gsa["vdop"] if gsa is not None else None
    if alt is None:
        ignore |= IGNORE_ALT
    if hdop is None:
        ignore |= IGNORE_HDOP
    if vdop is None:
        ignore |= IGNORE_VDOP

    has_pos = ft >= 2
    return {
        "time_usec": now_us,
        "gps_id": gps_id,
        "ignore_flags": ignore,
        "time_week_ms": week_ms,
        "time_week": week,
        "fix_type": ft,
        "lat": round(gga["lat"] * 1e7) if has_pos else 0,
        "lon": round(gga["lon"] * 1e7) if has_pos else 0,
        "alt": alt or 0.0,
        "hdop": hdop if hdop is not None else 0.0,
        "vdop": vdop if vdop is not None else 0.0,
        "vn": vn,
        "ve": ve,
        "vd": 0.0,
        "speed_accuracy": 0.0,
        "horiz_accuracy": 0.0,
        "vert_accuracy": 0.0,
        "satellites_visible": gga["sats"],
    }


class EpochAssembler:
    """Feed NMEA lines; returns GPS_INPUT kwargs when an epoch (GGA) arrives."""

    def __init__(self) -> None:
        self.rmc: dict | None = None
        self.gsa: dict | None = None

    def feed(self, line: str, now_us: int) -> dict | None:
        f = nmea_fields(line)
        if f is None:
            return None
        if f[0] == "RMC":
            self.rmc = parse_rmc(f)
        elif f[0] == "GSA":
            self.gsa = parse_gsa(f)
        elif f[0] == "GGA":
            gga = parse_gga(f)
            if gga is not None:
                return build_gps_input(gga, self.rmc, self.gsa, now_us)
        return None


def ubx_cfg_rate(hz: int) -> bytes:
    """UBX-CFG-RATE (nav rate, GPS time ref). RAM only, not saved to flash."""
    payload = (round(1000 / hz).to_bytes(2, "little")
               + (1).to_bytes(2, "little") + (1).to_bytes(2, "little"))
    body = bytes([0x06, 0x08]) + len(payload).to_bytes(2, "little") + payload
    ck_a = ck_b = 0
    for b in body:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return b"\xb5\x62" + body + bytes([ck_a, ck_b])


# ------------------------------------------------------------------- daemon

def find_device(pattern: str) -> str | None:
    matches = sorted(glob.glob(pattern))
    return matches[0] if matches else None


def run(device_glob: str, url: str, rate_hz: int) -> None:
    os.environ.setdefault("MAVLINK20", "1")
    import serial
    from pymavlink import mavutil

    mav = mavutil.mavlink_connection(
        url, source_system=SOURCE_SYSTEM, source_component=SOURCE_COMPONENT)
    log.info("sending GPS_INPUT to %s as %d/%d",
             url, SOURCE_SYSTEM, SOURCE_COMPONENT)

    while True:
        dev = find_device(device_glob)
        if dev is None:
            log.warning("no GPS matching %s; retrying", device_glob)
            time.sleep(2)
            continue
        try:
            with serial.Serial(dev, 9600, timeout=2) as port:
                log.info("opened %s", dev)
                if rate_hz:
                    port.write(ubx_cfg_rate(rate_hz))
                    log.info("requested %d Hz nav rate", rate_hz)
                asm = EpochAssembler()
                sent, last_fix = 0, None
                while True:
                    raw = port.readline()
                    if not raw:
                        log.warning("no data from %s", dev)
                        continue
                    msg = asm.feed(raw.decode("ascii", "replace"),
                                   int(time.time() * 1e6))
                    if msg is None:
                        continue
                    mav.mav.gps_input_send(**msg)
                    sent += 1
                    if msg["fix_type"] != last_fix:
                        log.info("fix_type=%d sats=%d hdop=%.2f",
                                 msg["fix_type"], msg["satellites_visible"],
                                 msg["hdop"])
                        last_fix = msg["fix_type"]
                    if sent % 3000 == 0:
                        log.info("sent %d GPS_INPUT", sent)
        except (serial.SerialException, OSError) as e:
            log.warning("GPS %s lost: %s; retrying", dev, e)
            time.sleep(2)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", default=DEFAULT_DEVICE_GLOB,
                    help="GPS serial device or glob (default: %(default)s)")
    ap.add_argument("--url", default=DEFAULT_URL,
                    help="pymavlink output URL (default: %(default)s)")
    ap.add_argument("--rate", type=int, default=DEFAULT_RATE_HZ,
                    help="GPS nav rate in Hz; 0 leaves it unchanged "
                         "(default: %(default)s)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run(args.device, args.url, args.rate)


if __name__ == "__main__":
    main()

