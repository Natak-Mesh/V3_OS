# modem73 live web UI — plan

Status: **proposal** (nothing implemented yet).

## Problem

- Setting the radio volume knob correctly is hard to judge. Today it means
  stopping `nucleus-modem73` and running the TUI over SSH to read Carrier, SNR
  and BER, or sending repeated test packets.
- Longer links will need a way to adapt to tougher RF conditions.

## What exists today

- `nucleus-modem73` runs headless. It exposes a KISS TCP port (`8001`, used by
  rnsd) and a **control port** (`reticulum.modem73_control_port`).
- nucleusd does not use the control port. The web UI only edits `modem73_*`
  config (RETICULUM > INTERFACES > CONFIGURE MODEM73) and applies it.
- The web UI already has a live-push pattern: a WebSocket with a polling
  fallback (`/api/v1/messaging/ws`, see `nucleusd/messaging/router.py` and
  `nucleusd/web/app.js`).

## modem73 control port (upstream `CONTROL_PORT.md`)

TCP JSON, 4-byte big-endian length prefix per message.

| Command / event | Use |
|---|---|
| `get_status` | channel state (idle/tx/rx), PTT, rx/tx/error counters, CRC and preamble errors, `last_snr`, `last_ber`, `ber_ema`, `occupancy_pct`, `population`, audio health |
| `get_config` / `set_config` | modulation, code rate, modem type (OFDM / MFSK / ROBUST), robust and MFSK modes, short frames, postamble, CSMA, `tx_drive`. Partial updates are allowed. |
| `tx` | send a raw payload |
| `send_beacon` | queue one presence tone |
| `rx_frame_history` | recent decoded frames; `since_seq` allows catch-up after reconnect |
| `rx_frame` event (pushed) | per decoded frame: `level_db` (dBFS), `snr`, `ber_pct`, `mode`, `size`, `callsign` |
| `config_changed` event (pushed) | any config change |

`tx_drive` (0.05–1.0) applies immediately with no restart.

## Goals

### 1. Set the radio volume without guesswork

- Live receive level (`level_db`), SNR and BER from each `rx_frame`, shown as
  gauges and coloured against the target (Carrier about −20 to −30 dB, per
  `docs/manual/reticulum.md`).
- Frame history table, plus "last frame N s ago" so stale readings are obvious.
- **Beacon** button (`send_beacon`) as a lightweight replacement for test
  packets: the far node sees level and SNR.
- **TX drive** slider (`tx_drive`), applied live, for tuning the transmit side
  while the far end watches SNR and BER.
- Works while the service is running, so no stopping it to use the TUI.

**Limitation:** the receive level is only reported when a frame decodes. The
partner must transmit for the gauge to move. There is no continuous audio meter.

### 2. Adapt to tougher RF

Staged, in increasing effort:

1. **Manual mode picker** with live SNR and BER and a Test button. Modes from
   `set_config`: OFDM modulation and code rate, MFSK, and robust RDM modes
   (down to about 99 bps).
2. **Advice:** the UI flags high BER, CRC errors or low SNR and suggests a more
   robust mode (or a faster one when margin is large).
3. **Automatic fallback:** a daemon steps the mode up or down from SNR, BER and
   CRC-error trends.

**To verify before stage 3:**

- Receive auto-detects the mode, but both ends must agree for transmit.
- Reticulum's interface bitrate (set from `modem73_mode`, see `nucleusd/schema.py`)
  must match the active mode.
- How the peers coordinate a mode change.

Stage 3 should wait for real range data.

## Architecture

```
browser ──WebSocket──▶ nucleusd modem73 router ──TCP/JSON──▶ modem73 control port
                       (single shared connection)
```

- New module `nucleusd/modem73/`: a control-port client (length-prefixed JSON,
  auto-reconnect) and a FastAPI router.
- One shared connection to the control port, fanned out to all browsers.
  Events are cached (latest status plus the last N frames) so a new page load
  has data immediately.
- Endpoints (proposed, under `/api/v1/modem73`):
  - `GET /status`: status, config and recent frames.
  - `WS /ws`: pushes `rx_frame`, status and config changes.
  - `POST /beacon`, `PUT /tx_drive`, `PUT /mode`.
- New page under RETICULUM > INTERFACES (next to CONFIGURE MODEM73), reusing
  the messaging WebSocket and polling-fallback pattern in `app.js`.
- Document the endpoints in `docs/API.md` and the page in
  `docs/manual/reticulum.md`.

## Design notes

- **Live vs persisted settings.** `set_config` changes are runtime only. They
  are lost on restart and must not fight `nucleusctl apply`, which renders
  flags from `config.yaml`. Options: a "Save to config" action that writes the
  `modem73_*` keys, or clearly labelled temporary overrides.
- **Hardware limits:** the control port is local, bound to `127.0.0.1`, and is
  not exposed through nginx directly. The browser reaches it only through
  nucleusd.
- **modem73 stopped or missing:** the page shows an offline state and retries.
- **Tuning flow:** the volume knob stays a manual adjustment (see
  `docs/manual/reticulum.md`); the UI only provides the feedback.
- **Version dependency:** the control port is upstream API. Check the installed
  modem73 version supports the commands used (for example `rx_frame_history`).

## Phases

1. Probe the control port on a node and confirm the installed version supports
   the commands above.
2. Backend client, router, caching, tests (`tests/test_web_routes.py` style).
3. Live page: gauges, frame history, beacon, `tx_drive`, mode picker.
4. Link-quality advice.
5. Automatic mode adaptation, after field range testing.

## Open questions

- Is the control port bound to localhost only on our installs?
- Which modes are worth exposing for VHF/UHF FM, and what mode set should the
  "tough RF" ladder use?
- How should peers agree on a mode change (manual on both ends, or negotiated)?
- Should live changes persist by default?
