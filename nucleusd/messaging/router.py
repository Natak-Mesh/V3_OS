"""FastAPI router for text messaging (mounted under /api/v1/messaging).

Thin HTTP layer over the nucleus-messaging daemon's UDP control socket
(127.0.0.1:5562). The daemon owns the single message store and both transports;
this only relays requests and returns its JSON. Mounted by nucleusd.api.
"""

from __future__ import annotations

import asyncio
import json
import socket

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
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


# How often we re-subscribe to the daemon (must stay under its SUB_TTL of 20s).
RESUBSCRIBE_SECS = 10.0


@router.websocket("/ws")
async def messages_ws(ws: WebSocket) -> None:
    """Live message push.

    Bridges the daemon's UDP push (see MessagingService._notify) to a browser
    WebSocket. We bind a UDP socket, subscribe to the daemon, forward its
    ``{"event":"message",...}`` datagrams, and re-subscribe periodically so the
    daemon keeps us in its subscriber set. On connect we also send the current
    history so the client can render immediately without a separate poll.
    """
    await ws.accept()
    loop = asyncio.get_event_loop()

    sub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sub.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sub.bind(("127.0.0.1", 0))          # ephemeral local port; daemon replies here
    sub.settimeout(0.0)                 # non-blocking; we await readability

    def _do_subscribe() -> None:
        try:
            sub.sendto(json.dumps({"cmd": "subscribe"}).encode("utf-8"), CONTROL_ADDR)
        except OSError:
            pass

    try:
        _do_subscribe()
        # Prime the client with existing history.
        try:
            hist = _rpc({"cmd": "history", "since": 0.0})
            await ws.send_text(json.dumps({"event": "history",
                                           "messages": hist.get("messages", [])}))
        except HTTPException:
            await ws.send_text(json.dumps({"event": "history", "messages": []}))

        last_sub = loop.time()
        while True:
            if loop.time() - last_sub >= RESUBSCRIBE_SECS:
                _do_subscribe()
                last_sub = loop.time()
            try:
                data = await asyncio.wait_for(loop.sock_recv(sub, 65535), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                obj = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if obj.get("event") == "message":
                await ws.send_text(json.dumps(obj))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        sub.close()
