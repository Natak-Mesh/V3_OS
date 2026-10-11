"""gpsbridge: NMEA parsing and GPS_INPUT building (off-box, no serial/mavlink)."""

from datetime import datetime, timezone

from nucleusd import gpsbridge as g

# Captured from the u-blox 7 on 0055-nucleus.
RMC = "$GPRMC,170344.00,A,3724.92779,N,07738.09797,W,0.224,,111026,,,A*6D"
GGA = "$GPGGA,170344.00,3724.92779,N,07738.09797,W,1,07,1.48,62.2,M,-34.9,M,,*5F"
TXT = "$GPTXT,01,01,02,u-blox ag - www.u-blox.com*50"
GSA_BODY = "GPGSA,A,3,04,05,09,12,,,,,,,,,2.50,1.48,2.01"


def _with_cs(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


GSA = _with_cs(GSA_BODY)
NOFIX_GGA = _with_cs("GPGGA,170345.00,,,,,0,00,99.99,,,,,,")


def test_checksum_rejects_corruption():
    assert g.nmea_fields(GGA)[0] == "GGA"
    assert g.nmea_fields(TXT)[0] == "TXT"
    assert g.nmea_fields(GGA.replace("62.2", "63.2")) is None
    assert g.nmea_fields("garbage") is None
    assert g.nmea_fields("$GPGGA,1,2") is None


def test_leading_binary_is_skipped():
    # A UBX ACK has no newline, so it arrives glued to the next NMEA line.
    assert g.nmea_fields("\ufffdb\x05\x01" + GGA + "\r\n")[0] == "GGA"


def test_parse_gga_and_latlon():
    gga = g.parse_gga(g.nmea_fields(GGA))
    assert abs(gga["lat"] - (37 + 24.92779 / 60)) < 1e-9
    assert abs(gga["lon"] + (77 + 38.09797 / 60)) < 1e-9
    assert (gga["quality"], gga["sats"], gga["hdop"], gga["alt"]) == \
        (1, 7, 1.48, 62.2)


def test_gps_week():
    # 2026-10-11 17:03:44 UTC is a Sunday -> start of GPS week 2440.
    week, ms = g.gps_week(datetime(2026, 10, 11, 17, 3, 44,
                                   tzinfo=timezone.utc))
    assert week == 2440
    assert ms == ((17 * 3600 + 3 * 60 + 44) + 18) * 1000
    assert g.gps_week(g.utc_from_nmea("111026", "170344.00")) == (week, ms)


def test_full_epoch_builds_gps_input():
    asm = g.EpochAssembler()
    assert asm.feed(TXT, 1) is None
    assert asm.feed(RMC, 1) is None
    assert asm.feed(GSA, 1) is None
    m = asm.feed(GGA, 123)
    assert m["time_usec"] == 123
    assert m["fix_type"] == 3
    assert m["lat"] == round((37 + 24.92779 / 60) * 1e7)
    assert m["lon"] == round(-(77 + 38.09797 / 60) * 1e7)
    assert (m["alt"], m["hdop"], m["vdop"], m["satellites_visible"]) == \
        (62.2, 1.48, 2.01, 7)
    assert m["time_week"] == 2440
    # Empty course -> velocity due north.
    assert abs(m["vn"] - 0.224 * g.KNOTS_TO_MS) < 1e-9 and m["ve"] == 0.0
    assert not m["ignore_flags"] & (g.IGNORE_VEL_HORIZ | g.IGNORE_ALT
                                    | g.IGNORE_HDOP | g.IGNORE_VDOP)
    assert m["ignore_flags"] & g.IGNORE_VEL_VERT


def test_velocity_from_course():
    rmc = _with_cs("GPRMC,170344.00,A,3724.92779,N,07738.09797,W,10.0,90.0,"
                   "111026,,,A")
    asm = g.EpochAssembler()
    asm.feed(rmc, 0)
    m = asm.feed(GGA, 0)
    assert abs(m["ve"] - 10.0 * g.KNOTS_TO_MS) < 1e-9
    assert abs(m["vn"]) < 1e-9


def test_stale_rmc_ignores_velocity_and_time():
    asm = g.EpochAssembler()
    asm.feed(RMC.replace("170344.00", "170343.00"), 0)  # bad cs -> dropped
    asm.rmc = g.parse_rmc(g.nmea_fields(RMC))
    asm.rmc["time"] = "170343.00"
    m = asm.feed(GGA, 0)
    assert m["ignore_flags"] & g.IGNORE_VEL_HORIZ
    assert (m["time_week"], m["time_week_ms"]) == (0, 0)


def test_no_fix():
    m = g.EpochAssembler().feed(NOFIX_GGA, 0)
    assert m["fix_type"] == 1
    assert (m["lat"], m["lon"], m["satellites_visible"]) == (0, 0, 0)
    assert m["ignore_flags"] & g.IGNORE_ALT


def test_2d_fix_from_gsa():
    asm = g.EpochAssembler()
    asm.feed(_with_cs(GSA_BODY.replace("A,3,", "A,2,")), 0)
    assert asm.feed(GGA, 0)["fix_type"] == 2


def test_ubx_cfg_rate_5hz():
    assert g.ubx_cfg_rate(5) == bytes.fromhex(
        "b562 0608 0600 c800 0100 0100 de6a".replace(" ", ""))
