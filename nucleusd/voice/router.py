"""FastAPI router for voice status/control (mounted under /api/v1/voice).

Thin HTTP layer over the nucleus-voice daemon's UDP control socket
(127.0.0.1:5556). The daemon owns the mesh audio, PTT and channel state; this
only relays STATUS / CHANNELS / CHANNEL <n> and returns the daemon's JSON.

The control protocol is text -> JSON (see voice/daemon.py control_thread):
    "STATUS"        -> full status (channel, ptt, sources, card, lora, ...)
    "CHANNELS"      -> {"current": N, "channels": [{"n":..,"label":..}, ...]}
    "CHANNEL <n>"   -> {"ok": true, "channel": N}

Mounted by nucleusd.api (audio itself never flows through here — that stays on
the daemon's own WebSocket at /voice-ws).
"""

from __future__ import annotations

import json
import socket

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

CONTROL_ADDR = ("127.0.0.1", 5556)
TIMEOUT = 2.0


class ChannelBody(BaseModel):
    n: int


def _rpc(command: str) -> dict:
    """One-shot text request / JSON reply against the daemon's control socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT)
    try:
        s.sendto(command.encode("utf-8"), CONTROL_ADDR)
        data, _ = s.recvfrom(65535)
        return json.loads(data.decode("utf-8"))
    except (socket.timeout, ConnectionRefusedError, OSError):
        raise HTTPException(status_code=503, detail="voice daemon unavailable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        s.close()


@router.get("/status")
def status() -> dict:
    return _rpc("STATUS")


@router.get("/channels")
def channels() -> dict:
    return _rpc("CHANNELS")


@router.post("/channel")
def set_channel(body: ChannelBody) -> dict:
    res = _rpc(f"CHANNEL {int(body.n)}")
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "bad channel"))
    return res
