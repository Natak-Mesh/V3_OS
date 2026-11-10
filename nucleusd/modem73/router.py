"""FastAPI router: modem73 live link tuning (mounted under /api/v1/modem73).

Thin HTTP layer over nucleusd.modem73.{control,mixer,advice}. Live changes
(TX level, sound-card gains) take effect immediately and last until the next
modem73 restart; POST /save writes them (and the mode) to config.yaml and runs
apply, which re-renders the launch script and restarts modem73 (+ rnsd when
the mode, hence the RNS bitrate, changed). Mode changes are save-only so the
RNS bitrate can never disagree with what the modem transmits.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .. import config as cfgio
from ..apply import apply as apply_config
from ..schema import MODEM73_MODES, NucleusConfig, modem73_mode_from_live
from . import advice as adv
from . import control as ctl
from . import mixer as mx

router = APIRouter(prefix="/api/v1/modem73", tags=["modem73"])

ModeKey = Literal[tuple(MODEM73_MODES)]  # type: ignore[valid-type]
MIXER_CACHE_S = 5.0
WS_TICK_S = 0.5
_mixer_cache: dict = {"ts": 0.0, "card": None, "rx_gain": None, "tx_gain": None}
_test_seq = 0


class TxLevelBody(BaseModel):
    tx_level: int = Field(ge=5, le=100)


class MixerBody(BaseModel):
    rx_gain: Optional[int] = Field(None, ge=0, le=35)
    tx_gain: Optional[int] = Field(None, ge=0, le=37)


class SaveBody(BaseModel):
    mode: Optional[ModeKey] = None
    tx_level: Optional[int] = Field(None, ge=5, le=100)
    rx_gain: Optional[int] = Field(None, ge=0, le=35)
    tx_gain: Optional[int] = Field(None, ge=0, le=37)


def _mixer(card: str, fresh: bool = False) -> dict:
    c = _mixer_cache
    if fresh or c["card"] != card or time.time() - c["ts"] > MIXER_CACHE_S:
        c.update(ts=time.time(), card=card,
                 rx_gain=mx.get(card, mx.RX_GAIN), tx_gain=mx.get(card, mx.TX_GAIN))
    return {"rx_gain": c["rx_gain"], "tx_gain": c["tx_gain"]}


def build_view(cfg: NucleusConfig, snap: dict, mixer: dict, now: float | None = None) -> dict:
    """Assemble the live-page payload from config + monitor snapshot. Pure."""
    now = time.time() if now is None else now
    r = cfg.reticulum
    live_cfg = snap.get("config") or {}
    drive = live_cfg.get("tx_drive")
    return {
        "online": snap["online"],
        "error": snap["error"],
        "callsign": cfg.modem73_callsign,
        "status": snap["status"],
        "status_age_s": round(now - snap["status_ts"], 1) if snap["status_ts"] else None,
        "live": {
            "mode": modem73_mode_from_live(live_cfg) if live_cfg else None,
            "modulation": live_cfg.get("modulation"),
            "code_rate": live_cfg.get("code_rate"),
            "payload_size": live_cfg.get("payload_size"),
            "tx_level": round(drive * 100) if isinstance(drive, (int, float)) else None,
            "rx_gain": mixer["rx_gain"],
            "tx_gain": mixer["tx_gain"],
        },
        "saved": {"mode": r.modem73_mode, "tx_level": r.modem73_tx_level,
                  "rx_gain": r.modem73_rx_gain, "tx_gain": r.modem73_tx_gain},
        "frames": snap["frames"],
        "attempts": snap["attempts"],
        "channel_db": snap["channel_db"],
        "channel_age_s": round(now - snap["channel_ts"], 1) if snap["channel_ts"] else None,
        "advice": adv.advise(snap["frames"], snap["status"], now=now,
                             attempts=snap["attempts"]),
        "modes": MODEM73_MODES,
    }


def _view(fresh_mixer: bool = False) -> dict:
    cfg = cfgio.load()
    mon = ctl.monitor()
    mon.touch()
    return build_view(cfg, mon.snapshot(), _mixer(cfg.reticulum.modem73_alsa_card, fresh_mixer))


def _ctl(cmd: str, **params) -> dict:
    try:
        res = ctl.request(cmd, **params)
    except ctl.Modem73Error as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=res.get("error") or f"modem73 rejected {cmd}")
    return res


@router.get("/status")
def status() -> dict:
    return _view(fresh_mixer=True)


@router.post("/test")
def send_test() -> dict:
    """Transmit one test frame now (far end sees level/SNR/BER for it).

    Payload is modem73's native text-message format ("M73:<call>:<text>"),
    which modem73 logs on receipt; rnsd discards it (not a valid RNS packet).
    """
    global _test_seq
    _test_seq += 1
    call = cfgio.load().modem73_callsign
    text = f"M73:{call}:nucleus test {_test_seq}".encode()
    _ctl("tx", data=base64.b64encode(text).decode(), oper_mode=-1)
    return {"ok": True, "seq": _test_seq, "bytes": len(text)}


@router.put("/tx_level")
def put_tx_level(body: TxLevelBody) -> dict:
    """Live TX level (modem73 tx_drive); not persisted until POST /save."""
    _ctl("set_config", tx_drive=body.tx_level / 100)
    return {"ok": True, "tx_level": body.tx_level}


@router.put("/mixer")
def put_mixer(body: MixerBody) -> dict:
    """Live sound-card gains; not persisted until POST /save."""
    card = cfgio.load().reticulum.modem73_alsa_card
    try:
        if body.rx_gain is not None:
            mx.set_value(card, mx.RX_GAIN, body.rx_gain)
        if body.tx_gain is not None:
            mx.set_value(card, mx.TX_GAIN, body.tx_gain)
    except mx.MixerError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"ok": True, **_mixer(card, fresh=True)}


@router.post("/save")
def save(body: SaveBody) -> dict:
    """Persist mode/levels to config.yaml and apply (restarts modem73)."""
    cfg = cfgio.load()
    data = cfg.model_dump(mode="json", exclude_none=True)
    for key in ("mode", "tx_level", "rx_gain", "tx_gain"):
        val = getattr(body, key)
        if val is not None:
            data["reticulum"][f"modem73_{key}"] = val
    try:
        new = NucleusConfig.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    cfgio.save(new)
    result = apply_config(new)
    return {"ok": True, "changed": result.changed, "units_restarted": result.units_restarted}


@router.websocket("/ws")
async def live_ws(ws: WebSocket) -> None:
    """Push the live view whenever the monitor cache changes."""
    await ws.accept()
    last = None
    try:
        while True:
            mon = ctl.monitor()
            mon.touch()
            v = mon.snapshot()["version"]
            if v != last:
                last = v
                view = await asyncio.to_thread(_view)
                await ws.send_text(json.dumps({"event": "modem73", "view": view}))
            await asyncio.sleep(WS_TICK_S)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
