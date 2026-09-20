"""FastAPI router: imperative Tailscale connection control for the web UI.

Thin HTTP layer over tailscale.py. All CLI work lives in that module; this maps
verbs to routes and translates TailscaleError to HTTP 503. Mounted under
/api/v1/tailscale by nucleusd.api. Tailscale is intentionally outside the
config.yaml/apply pipeline — it's on/off + tailnet switching, driven live here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import tailscale as ts

router = APIRouter(prefix="/api/v1/tailscale", tags=["tailscale"])


class UpBody(BaseModel):
    authkey: str | None = None
    login_server: str | None = None


class SwitchBody(BaseModel):
    account: str = ""


def _handle(fn, *args):
    try:
        return fn(*args)
    except ts.TailscaleError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/status")
def status() -> dict:
    return _handle(ts.status)


@router.get("/accounts")
def accounts() -> dict:
    return {"accounts": _handle(ts.accounts)}


@router.post("/up")
def up(body: UpBody | None = None) -> dict:
    body = body or UpBody()
    return _handle(ts.up, body.authkey or None, body.login_server or None)


@router.post("/down")
def down() -> dict:
    return _handle(ts.down)


@router.post("/logout")
def logout() -> dict:
    return _handle(ts.logout)


@router.post("/switch")
def switch(body: SwitchBody) -> dict:
    return _handle(ts.switch, body.account)
