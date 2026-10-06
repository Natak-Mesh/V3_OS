"""FastAPI router: read-only Reticulum (rnsd) status for the web UI.

Thin HTTP layer over nucleusd.reticulum. All RPC work lives in that module;
this maps routes to its functions and translates ReticulumError to HTTP 503.
Mounted under /api/v1/reticulum by nucleusd.api. Read-only by design — this is
runtime status (like /api/v1/status), not part of the config/apply pipeline.

Node discovery (the Nucleus contact list) is NOT here any more — it moved to the
messaging daemon's contact book, exposed at /api/v1/messaging/rns/contacts,
because nodes are now found by importing contact cards rather than by a flooded
custom announce.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import reticulum as reti

router = APIRouter(prefix="/api/v1/reticulum", tags=["reticulum"])


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
