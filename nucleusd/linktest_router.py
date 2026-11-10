"""FastAPI router: on-demand link tests (ping / iperf3 / rnprobe).

Thin HTTP layer over nucleusd.linktest. Mounted under /api/v1/linktest by
nucleusd.api. Runtime diagnostics, outside the config/apply pipeline. Every
test runs only when requested; nothing is left running afterwards.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import config as cfgio
from . import linktest as lt

router = APIRouter(prefix="/api/v1/linktest", tags=["linktest"])

Link = Literal["wifi", "halow"]


class PingBody(BaseModel):
    link: Link
    peer_id: int = Field(ge=1, le=254)
    count: int = Field(5, ge=1, le=20)


class IperfBody(BaseModel):
    link: Link
    peer_id: int = Field(ge=1, le=254)
    secs: int = Field(5, ge=1, le=lt.MAX_SECS)
    reverse: bool = False


class ServerBody(BaseModel):
    link: Link
    secs: int = Field(5, ge=1, le=lt.MAX_SECS)


class ProbeBody(BaseModel):
    dest: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    # Worst case (path wait + probes x (timeout + 1 s wait)) stays under
    # linktest.RNPROBE_HARD_TIMEOUT and nginx's 60 s proxy timeout.
    probes: int = Field(3, ge=1, le=3)
    timeout: int = Field(10, ge=1, le=10)


def _handle(fn, *args):
    try:
        return fn(*args)
    except lt.LinkTestBadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except lt.LinkTestBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except lt.LinkTestError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/ping")
def ping(body: PingBody) -> dict:
    return _handle(lt.ping, cfgio.load(), body.link, body.peer_id, body.count)


@router.post("/iperf")
def iperf(body: IperfBody) -> dict:
    return _handle(lt.iperf, cfgio.load(), body.link, body.peer_id, body.secs,
                   body.reverse)


@router.post("/iperf-server")
def iperf_server(body: ServerBody, request: Request) -> dict:
    """Peer side of /iperf: one-shot iperf3 server. Mesh peers/localhost only."""
    cfg = cfgio.load()
    host = request.client.host if request.client else ""
    if not lt.caller_allowed(cfg, host):
        raise HTTPException(status_code=403, detail="mesh peers only")
    return _handle(lt.start_server, cfg, body.link, body.secs)


@router.post("/rnprobe")
def rnprobe(body: ProbeBody) -> dict:
    return _handle(lt.rnprobe, body.dest, body.probes, body.timeout)
