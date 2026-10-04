"""FastAPI router: read-only Reticulum (rnsd) status for the web UI.

Thin HTTP layer over nucleusd.reticulum. All RPC work lives in that module;
this maps routes to its functions and translates ReticulumError to HTTP 503.
Mounted under /api/v1/reticulum by nucleusd.api. Read-only by design — this is
runtime status (like /api/v1/status), not part of the config/apply pipeline.
"""

from __future__ import annotations

import json
import socket

from fastapi import APIRouter, HTTPException

from . import reticulum as reti

router = APIRouter(prefix="/api/v1/reticulum", tags=["reticulum"])

# The discovered Nucleus-node list is owned by the messaging daemon's RNS lane
# (it hears the nucleus.node announces). We relay one query to its control
# socket rather than open a second RNS stack here.
_MSG_CONTROL_ADDR = ("127.0.0.1", 5562)
_MSG_TIMEOUT = 2.0


def _handle(fn, *args):
    try:
        return fn(*args)
    except reti.ReticulumError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/status")
def status() -> dict:
    return _handle(reti.status)


@router.get("/interfaces")
def interfaces() -> dict:
    return {"interfaces": _handle(reti.interfaces)}


@router.get("/paths")
def paths() -> dict:
    return {"paths": _handle(reti.path_table)}


@router.get("/nodes")
def nodes() -> dict:
    """Nucleus nodes discovered via their nucleus.node announces.

    Sourced from the messaging daemon's RNS lane. Returns an empty list (not an
    error) when the lane is disabled or the daemon is unreachable, so the UI can
    render a clean "none discovered" state.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(_MSG_TIMEOUT)
    try:
        s.sendto(json.dumps({"cmd": "rns_peers"}).encode("utf-8"), _MSG_CONTROL_ADDR)
        data, _ = s.recvfrom(65535)
        reply = json.loads(data.decode("utf-8"))
        return {"nodes": reply.get("peers", [])}
    except (socket.timeout, ConnectionRefusedError, OSError, ValueError):
        return {"nodes": []}
    finally:
        s.close()
