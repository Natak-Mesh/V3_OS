"""FastAPI router for text messaging (mounted under /api/v1/messaging).

Thin HTTP layer over the nucleus-messaging daemon's UDP control socket
(127.0.0.1:5562). The daemon owns the single message store and both transports;
this only relays requests and returns its JSON. Mounted by nucleusd.api.
"""

from __future__ import annotations

import asyncio
import json
import socket

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/messaging", tags=["messaging"])

CONTROL_ADDR = ("127.0.0.1", 5562)
TIMEOUT = 2.0


class SendBody(BaseModel):
    text: str = ""


class RnsSendBody(BaseModel):
    dest: str = ""   # recipient lxmf.delivery hash (hex)
    text: str = ""


class RnsImportBody(BaseModel):
    link: str | None = None    # a nucleus-rns:// card link (or bare token)
    card: dict | None = None   # or the raw card dict (e.g. pulled over the mesh)
    source: str = "link"       # provenance tag: link / qr / mesh


def _rpc(req: dict, timeout: float = TIMEOUT) -> dict:
    """One-shot request/reply against the daemon's UDP control socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
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


# ── Reticulum/LXMF direct-message lane ───────────────────────────
# Per-contact DMs over LXMF (via rnsd), distinct from the WiFi/LoRa broadcast
# log above. Contacts are explicit now (no custom announce): import a peer's
# card over the mesh / as a link / via QR, or let an inbound message auto-add
# the sender. The daemon owns the lane + stores; these just relay control
# commands. All return cleanly (empty lists / disabled flags) when the lane is
# off, so the UI can render without special-casing.
@router.get("/rns/status")
def rns_status() -> dict:
    return _rpc({"cmd": "status"}).get("rns", {"enabled": False})


@router.get("/rns/contacts")
def rns_contacts() -> dict:
    """Imported/known contacts (replaces the old announce-discovered peers)."""
    return _rpc({"cmd": "rns_contacts"})


@router.post("/rns/contacts")
def rns_import(body: RnsImportBody) -> dict:
    """Import a contact from a nucleus-rns:// link, a raw card, or a mesh pull."""
    res = _rpc({"cmd": "rns_import", "link": body.link,
                "card": body.card, "source": body.source})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "import failed"))
    return res


@router.get("/rns/mesh-peers")
def rns_mesh_peers() -> dict:
    """Nucleus nodes reachable over the WiFi mesh, each with its contact card.

    Mirrors the Meshtastic one-click-join discovery: find neighbours from Babel,
    pull each one's ``/rns/card`` over HTTP, and return the verifiable cards so
    the UI can offer a one-tap "Add". Nodes that don't answer / have the lane off
    are simply omitted. No card is trusted here — import verifies address↔key.
    """
    from .. import mesh_peers
    out = []
    for ip in mesh_peers.babel_peer_ips():
        data = mesh_peers.fetch_peer_json(ip, "/api/v1/messaging/rns/card")
        card = (data or {}).get("card")
        link = (data or {}).get("link")
        if card and link:
            out.append({"ip": ip, "card": card, "link": link})
    return {"ok": True, "peers": out}


@router.delete("/rns/contacts/{dest}")
def rns_remove(dest: str) -> dict:
    res = _rpc({"cmd": "rns_remove", "dest": dest})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "remove failed"))
    return res


@router.get("/rns/card")
def rns_card() -> dict:
    """This node's own shareable contact card + its nucleus-rns:// link."""
    return _rpc({"cmd": "rns_card"})


@router.get("/rns/card/qr")
def rns_card_qr() -> Response:
    """SVG QR code of this node's card link, scannable by another node."""
    res = _rpc({"cmd": "rns_card"})
    link = res.get("link")
    if not link:
        raise HTTPException(status_code=503, detail=res.get("error", "card not ready"))
    from . import rns_proto as proto
    try:
        svg = proto.card_qr_svg(link)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-cache"})


@router.post("/rns/announce")
def rns_announce() -> dict:
    """Emit this node's lxmf.delivery announce once (operator-triggered)."""
    res = _rpc({"cmd": "rns_announce"})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "announce failed"))
    return res


@router.get("/rns/contacts/{dest}/path")
def rns_path(dest: str) -> dict:
    """Read-only path-table state for a contact (known/hops/interface).

    Reads rnsd's local path table only — emits no radio traffic. Pair ``known``
    with the contact's last_seen for path age in the UI.
    """
    return _rpc({"cmd": "rns_path", "dest": dest})


@router.post("/rns/contacts/{dest}/path")
def rns_request_path(dest: str) -> dict:
    """Operator-triggered: emit ONE path request for a contact.

    The only thing that makes a contact reachable with announces off. Sends a
    single path request over every RNS interface; does not retry.
    """
    # The daemon drops the stale path, waits 2 s for rnsd to cull it, then
    # requests — so this call blocks longer than the default timeout.
    res = _rpc({"cmd": "rns_request_path", "dest": dest}, timeout=8.0)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "path request failed"))
    return res


@router.get("/rns/messages")
def rns_messages(peer: str | None = None, since: float = 0.0) -> dict:
    return _rpc({"cmd": "rns_history", "peer": peer, "since": since})


@router.post("/rns/messages")
def rns_send(body: RnsSendBody) -> dict:
    res = _rpc({"cmd": "rns_send", "dest": body.dest, "text": body.text})
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
