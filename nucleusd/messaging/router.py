"""FastAPI router for text messaging (mounted under /api/v1/messaging).

Thin HTTP layer over the nucleus-messaging daemon's UDP control socket
(127.0.0.1:5562). The daemon owns the single message store and both transports;
this only relays requests and returns its JSON. Mounted by nucleusd.api.
"""

from __future__ import annotations

import json
import socket

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/messaging", tags=["messaging"])

CONTROL_ADDR = ("127.0.0.1", 5562)
TIMEOUT = 2.0


class SendBody(BaseModel):
    text: str = ""


def _rpc(req: dict) -> dict:
    """One-shot request/reply against the daemon's UDP control socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT)
    try:
        s.sendto(json.dumps(req).encode("utf-8"), CONTROL_ADDR)
        data, _ = s.recvfrom(65535)
        return json.loads(data.decode("utf-8"))
    except (socket.timeout, ConnectionRefusedError, OSError):
        raise HTTPException(status_code=503, detail="messaging daemon unavailable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        s.close()


@router.get("/status")
def status() -> dict:
    return _rpc({"cmd": "status"})


@router.get("/messages")
def messages(since: float = 0.0) -> dict:
    return _rpc({"cmd": "history", "since": since})


@router.post("/messages")
def send(body: SendBody) -> dict:
    res = _rpc({"cmd": "send", "text": body.text})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "send failed"))
    return res
