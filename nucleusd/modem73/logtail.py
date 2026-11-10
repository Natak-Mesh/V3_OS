"""Decoder detail from modem73's log, which the control port does not expose.

The control port reports a frame only once it decodes. The TUI also shows
attempts that fail CRC, per-symbol SNR, CFO/SFO and the channel level; modem73
prints those to stdout (nucleus-modem73.service -> /var/log/modem73.log), so
they are parsed here. `Parser` is pure (tested on real log lines); `LogTail`
only tracks the file offset.
"""

from __future__ import annotations

import os
import re
from collections import deque

LOG_PATH = "/var/log/modem73.log"
MAX_ATTEMPTS = 32
# Larger jumps (first open, rotation) re-read only this tail, with unknown times.
BACKFILL_BYTES = 64 * 1024

_LINES = [
    ("sync", re.compile(r"Decoder: Sync found")),
    ("cfo", re.compile(r"Decoder: CFO = (\S+) Hz")),
    ("sfo", re.compile(r"Decoder: SFO = (\S+) ppm")),
    ("call", re.compile(r"Decoder: Call sign:\s*(\S*)")),
    ("modulation", re.compile(r"modulation: (\S+)")),
    ("code_rate", re.compile(r"code rate: (\S+)")),
    ("sym", re.compile(r"Decoder: Symbol \d+ SNR = (\S+) dB")),
    ("crc", re.compile(r"Decoder: CRC failed")),
    ("ok", re.compile(r"Decoder: Frame decoded \d+ bytes, SNR=(\S+) dB, BER=(\S+)%")),
    ("tx", re.compile(r"TX: \d+ bytes")),
    ("chan", re.compile(r"CSMA: Channel (?:clear|busy) \((\S+) dB")),
]


class Parser:
    """modem73 log lines -> frame attempts + last channel level."""

    def __init__(self) -> None:
        self.attempts: deque = deque(maxlen=MAX_ATTEMPTS)
        self.channel_db: float | None = None
        self.channel_ts: float | None = None
        self._cur: dict | None = None

    def _new(self, now: float | None) -> dict:
        self._cur = {"time": now, "callsign": None, "modulation": None, "code_rate": None,
                     "cfo_hz": None, "sfo_ppm": None, "syms": [], "ok": None,
                     "snr": None, "ber_pct": None}
        self.attempts.append(self._cur)
        return self._cur

    def feed(self, line: str, now: float | None) -> bool:
        """Fold one line in; True if anything changed. `now` None = time unknown."""
        for kind, rx in _LINES:
            m = rx.match(line)
            if m:
                break
        else:
            return False
        a = self._cur
        if kind == "chan":
            self.channel_db, self.channel_ts = float(m[1]), now
        elif kind == "tx":
            self._cur = None  # the TX block's modulation/code rate lines are ours, not RX
        elif kind == "sync" or (kind == "sfo" and (a is None or a["syms"])):
            # Back-to-back frames re-sync without a "Sync found" line: SFO starts them.
            a = self._new(now)
            if kind == "sfo":
                a["sfo_ppm"] = float(m[1])
        elif a is None:
            return False
        elif kind == "cfo":
            a["cfo_hz"] = float(m[1])
        elif kind == "sfo":
            a["sfo_ppm"] = float(m[1])
        elif kind == "call":
            a["callsign"] = m[1]
        elif kind in ("modulation", "code_rate"):
            if a["syms"]:
                return False
            a[kind] = m[1]
        elif kind == "sym":
            a["syms"].append(float(m[1]))
        elif kind == "crc":
            a["ok"] = False
        elif kind == "ok":
            a.update(ok=True, snr=float(m[1]), ber_pct=float(m[2]))
        return True

    def view(self) -> list[dict]:
        """Attempts newest first; per-symbol SNR reduced to avg/min."""
        out = []
        for a in reversed(self.attempts):
            s = a["syms"]
            out.append({**{k: v for k, v in a.items() if k != "syms"},
                        "sym_snr": round(sum(s) / len(s), 1) if s else None,
                        "sym_snr_min": round(min(s), 1) if s else None})
        return out


class LogTail:
    """Incremental reader of the modem73 log (complete lines only)."""

    def __init__(self, path: str = LOG_PATH) -> None:
        self.path = path
        self._pos: int | None = None

    def read(self) -> tuple[list[str], bool]:
        """New lines since the last call, and whether they are a backfill."""
        try:
            with open(self.path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                pos = self._pos
                backfill = pos is None or size < pos or size - pos > BACKFILL_BYTES
                if backfill:
                    pos = max(0, size - BACKFILL_BYTES)
                f.seek(pos)
                data = f.read(size - pos)
        except OSError:
            return [], False
        start = data.find(b"\n") + 1 if backfill and pos > 0 else 0
        end = data.rfind(b"\n") + 1
        self._pos = pos + max(end, start)
        return data[start:end].decode("utf-8", "replace").splitlines(), backfill
