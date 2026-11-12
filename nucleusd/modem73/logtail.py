"""Frame attempts from modem73's log, incl. CRC failures.

The control port reports a frame only once it decodes; frames that fail CRC
appear only in modem73's stdout (/var/log/modem73.log), with per-symbol SNR.
`Parser` is pure (tested on real log lines); `LogTail` tracks the file offset.
"""

from __future__ import annotations

import os
import re
from collections import deque

LOG_PATH = "/var/log/modem73.log"
MAX_ATTEMPTS = 10
# Larger jumps (first open, rotation) re-read only this tail, with unknown times.
BACKFILL_BYTES = 64 * 1024

_LINES = [
    ("sync", re.compile(r"Decoder: Sync found")),
    ("sfo", re.compile(r"Decoder: SFO = ")),
    ("call", re.compile(r"Decoder: Call sign:\s*(\S*)")),
    ("modulation", re.compile(r"modulation: (\S+)")),
    ("code_rate", re.compile(r"code rate: (\S+)")),
    ("sym", re.compile(r"Decoder: Symbol \d+ SNR = (\S+) dB")),
    ("crc", re.compile(r"Decoder: CRC failed")),
    ("ok", re.compile(r"Decoder: Frame decoded \d+ bytes, SNR=(\S+) dB, BER=(\S+)%")),
    ("tx", re.compile(r"TX: \d+ bytes")),
]


class Parser:
    """modem73 log lines -> frame attempts."""

    def __init__(self) -> None:
        self.attempts: deque = deque(maxlen=MAX_ATTEMPTS)
        self._cur: dict | None = None

    def feed(self, line: str, now: float | None) -> bool:
        """Fold one line in; True if anything changed. `now` None = time unknown."""
        for kind, rx in _LINES:
            m = rx.match(line)
            if m:
                break
        else:
            return False
        a = self._cur
        if kind == "tx":
            self._cur = None  # the TX block's modulation/code rate lines are ours, not RX
        elif kind == "sync" or kind == "sfo":
            # Back-to-back frames start at SFO with no "Sync found" line.
            if kind == "sfo" and a is not None and not a["syms"]:
                return False
            # A sync that never got a callsign was hiss; drop it so noise
            # can't push real frames out of the short list.
            if a is not None and not a["callsign"] and self.attempts and self.attempts[-1] is a:
                self.attempts.pop()
            self._cur = {"time": now, "callsign": None, "modulation": None,
                         "code_rate": None, "syms": [], "ok": None, "snr": None,
                         "ber_pct": None}
            self.attempts.append(self._cur)
        elif a is None:
            return False
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
            a.update(ok=True, snr=round(float(m[1]), 1), ber_pct=float(m[2]))
        return True

    def clear(self) -> None:
        """Forget all attempts, incl. one in progress."""
        self.attempts.clear()
        self._cur = None

    def view(self) -> list[dict]:
        """Attempts newest first. `snr`: modem73's decoded SNR, or for a CRC
        failure (which has none) the mean per-symbol SNR."""
        out = []
        for a in reversed(self.attempts):
            s = a["syms"]
            snr = a["snr"] if a["snr"] is not None else (round(sum(s) / len(s), 1) if s else None)
            out.append({**{k: v for k, v in a.items() if k != "syms"}, "snr": snr})
        return out


class LogTail:
    """Incremental reader of the modem73 log (complete lines only)."""

    def __init__(self, path: str = LOG_PATH) -> None:
        self.path = path
        self._pos: int | None = None

    def seek_end(self) -> tuple[int, int | None]:
        """Skip everything already in the file (no backfill on the next read).
        Returns (offset, inode) so the position can be resumed after a restart."""
        try:
            st = os.stat(self.path)
            self._pos, ino = st.st_size, st.st_ino
        except OSError:
            self._pos, ino = 0, None
        return self._pos, ino

    def resume(self, pos: int, ino: int | None) -> None:
        """Continue from a position saved by seek_end(). Ignored if the log was
        rotated (other inode) or truncated since; the next read backfills."""
        try:
            st = os.stat(self.path)
        except OSError:
            return
        if ino is not None and st.st_ino == ino and 0 <= pos <= st.st_size:
            self._pos = pos

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
