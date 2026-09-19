"""FastAPI router exposing the Meshtastic radio configurator + bridge status.

Thin HTTP layer over meshtastic_api.py (the ported V2 radio logic). All the
radio work lives in that module; this only maps functions to routes and
translates its exceptions to HTTP status codes. Mounted under /api/v1/meshtastic
by nucleusd.api.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import meshtastic_api as mx

router = APIRouter(prefix="/api/v1/meshtastic", tags=["meshtastic"])


class ApplyBody(BaseModel):
    changes: dict = {}


class ChannelUrlBody(BaseModel):
    url: str = ""


class JoinPeerBody(BaseModel):
    host: str = ""


def _handle(fn, *args):
    """Call a meshtastic_api helper, mapping its exceptions to HTTP errors."""
    try:
        return fn(*args)
    except mx.RadioBadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except mx.RadioBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except mx.RadioNotReady as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/status")
def status() -> dict:
    return mx.status()


@router.get("/logs")
def logs() -> dict:
    return mx.bridge_logs()


@router.get("/config")
def config_cached() -> dict:
    return mx.config_cached()


@router.get("/config/op-status")
def op_status() -> dict:
    return mx.config_op_status()


@router.post("/config/read")
def config_read() -> dict:
    return _handle(mx.config_read)


@router.post("/config/apply")
def config_apply(body: ApplyBody) -> dict:
    return _handle(mx.config_apply, body.changes)


@router.post("/config/channel-url")
def config_channel_url(body: ChannelUrlBody) -> dict:
    return _handle(mx.config_channel_url, body.url)


@router.get("/nodes")
def nodes() -> dict:
    """LoRa nodes this radio has recently heard over RF (from the bridge dump)."""
    return mx.nodes()


@router.get("/peers")
def peers() -> dict:
    return mx.peers()


@router.post("/config/join-peer")
def config_join_peer(body: JoinPeerBody) -> dict:
    return _handle(mx.config_join_peer, body.host)


@router.get("/config/qr")
def config_qr() -> Response:
    svg = _handle(mx.config_qr_svg)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-cache"})
