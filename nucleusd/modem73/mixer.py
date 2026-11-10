"""Sound-card mixer (ALSA) for the modem73 radio interface.

The launch script pins these controls from config on every modem73 start; this
module reads them and changes them LIVE (until the next modem73 restart) for
tuning from the web UI. Control names are those of the DigiRig's C-Media codec.
"""

from __future__ import annotations

import re
import subprocess

RX_GAIN = "Mic Capture Volume"       # 0-35 = -12..+23 dB
TX_GAIN = "Speaker Playback Volume"  # 0-37 = -37..0 dB
TIMEOUT = 3


class MixerError(Exception):
    pass


def parse_cget(out: str) -> int | None:
    """First value from `amixer cget` output (': values=17,17' -> 17)."""
    m = re.search(r"^\s*:\s*values=(\d+)", out, re.M)
    return int(m.group(1)) if m else None


def get(card: str, name: str) -> int | None:
    """Current raw step of a mixer control, or None if unreadable."""
    try:
        r = subprocess.run(["amixer", "-c", card, "cget", f"name={name}"],
                           capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_cget(r.stdout) if r.returncode == 0 else None


def set_value(card: str, name: str, value: int) -> None:
    try:
        r = subprocess.run(["amixer", "-q", "-c", card, "cset", f"name={name}", str(value)],
                           capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MixerError(f"amixer failed: {e}") from e
    if r.returncode != 0:
        raise MixerError((r.stderr or r.stdout).strip() or f"amixer exit {r.returncode}")
