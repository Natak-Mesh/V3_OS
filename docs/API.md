# Nucleus V3 OS — HTTP API

Reference for every HTTP endpoint the node serves. Current version: **0.8.0**.

## For the newbie: how to use this

The node runs one always-on web server (`nucleusd`, a Python/FastAPI app). It
serves two things from the same place:

- The **web UI** (the pages you click) at `http://<node>/`
- The **API** (the same operations, as JSON) under `http://<node>/api/v1/...`

The UI is just a website that calls this API — so anything the UI does, you can
do from a script or the command line.

**Base URL.** From the node itself use `http://localhost:8080`. From another
machine on the mesh use `http://10.20.<id>.1` or `http://10.20.1.<id>` (nginx
listens on port 80 and forwards to nucleusd on 8080). This node is id 42.

**No login.** There is no auth token; access is controlled by being on the
network. Don't expose it to the open internet.

**Talking to it.** Every endpoint speaks JSON. The tool in the examples is
`curl`:

- `-s` = quiet, `-m 4` = give up after 4s.
- `GET` (the default) reads something.
- `POST`/`PUT` change something; send a JSON body with
  `-H 'Content-Type: application/json' -d '{...}'`.

Try this first — it should print `{"version":"0.8.0"}`:

```bash
curl -s http://localhost:8080/api/v1/version
```

Pipe JSON through `python3 -m json.tool` (or `jq`) to read it nicely.

**Two ways to explore live.** FastAPI auto-generates docs from the code, so
these are always in sync with the running build:

- Interactive "try it" UI: `http://localhost:8080/docs`
- Raw machine-readable spec: `http://localhost:8080/openapi.json`

**HTTP status codes you'll see:** `200` ok · `400` bad input · `404` not
ready · `409` busy (radio in use) · `422` config failed validation · `503`
a backend daemon (messaging/voice) isn't running · `500` other error.

---

## Core (`/api/v1`)

Served directly by `nucleusd`. Always available.

### `GET /api/v1/version`
Running OS version.
```bash
curl -s http://localhost:8080/api/v1/version
# {"version":"0.8.0"}
```

### `GET /api/v1/config`
Full validated config plus a `_derived` block of computed values.
```bash
curl -s http://localhost:8080/api/v1/config
```

### `PUT /api/v1/config`
Validate and save a **complete** replacement config. Does *not* apply it (see
`/apply`). Returns `422` if the body fails validation.
```bash
curl -s -X PUT http://localhost:8080/api/v1/config \
  -H 'Content-Type: application/json' -d @config.json
# {"ok":true,"hostname":"0042-nucleus"}
```

### `POST /api/v1/apply`
Render templates and (re)start affected services from the saved config.
Query param `dry_run=true` reports what *would* change without touching
anything.
```bash
curl -s -X POST 'http://localhost:8080/api/v1/apply?dry_run=true'
# {"dry_run":true,"changed":[...],"units_restarted":[...]}
```

### `GET /api/v1/status`
Live runtime status: interfaces + addresses, service states, and Babel mesh
neighbours/routes.
```bash
curl -s http://localhost:8080/api/v1/status
# {"interfaces":{"wlan1":{...}}, "services":{"babeld":"active",...},
#  "babel_neighbours":[{"ipv4":"10.20.1.46","link_pct":94,...}], ...}
```

### `GET /api/v1/update/check`
Compare installed version against the git remote.
```bash
curl -s http://localhost:8080/api/v1/update/check
# {"installed":"0.8.0","available":"0.8.0","behind":0,"dirty":false,
#  "offline":false,"local_head":"18f28e1","remote_head":"18f28e1","error":null}
```

### `POST /api/v1/update/start`
Kick off the node update in the background (detached via systemd-run).
```bash
curl -s -X POST http://localhost:8080/api/v1/update/start
```

### `GET /api/v1/update/progress`
Poll update state + log while an update runs.
```bash
curl -s http://localhost:8080/api/v1/update/progress
```


---

## Meshtastic radio (`/api/v1/meshtastic`)

Configures the LoRa radio and the CoT bridge. Available only on nodes with the
radio deps installed. Errors: `400` bad request · `404` radio not ready ·
`409` radio busy.

### `GET /status`
```bash
curl -s http://localhost:8080/api/v1/meshtastic/status
# {"bridge_enabled":true,"service_active":true,"service_enabled":true,"radio_detected":true}
```

### `GET /logs`
Recent bridge log lines.

### Configuring the radio — read this first

Radio reads and writes are **asynchronous**. `POST /config/read` and
`POST /config/apply` do not block; they start a background job and return
`{"success":true,"started":true}` immediately. Applying a change **reboots the
radio once per config group**, so a full apply takes ~30–120s. You must poll
`GET /config/op-status` until it finishes, then `GET /config` for the result.

Only one radio operation runs at a time — a second one while another is in
progress returns `409`. If no radio is present you get `404`.

**Recommended flow for a config app:**

1. `POST /config/read` — pull current settings from the radio.
2. Poll `GET /config/op-status` until `status` is `done` (or `error`).
3. `GET /config` — show the user the current values.
4. `POST /config/apply` with the changed fields (see field table below).
5. Poll `GET /config/op-status` again until `done`.
6. `GET /config` — confirm the radio now reflects the changes.

### `GET /config`
Last-read cached radio config (does **not** touch the radio; safe to call
often). `busy` mirrors whether an op is running.
```bash
curl -s http://localhost:8080/api/v1/meshtastic/config
# {"config":{"owner":"0042_nuc","owner_short":"0042","region":"US",
#   "modem_preset":"SHORT_FAST","frequency_slot":0,"hop_limit":3,"tx_power":30,
#   "role":"TAK","channel_url":"https://meshtastic.org/e/#Ci0S...",
#   "channels":[{"index":0,"name":"Natak","has_psk":true,
#     "psk_fingerprint":"4baadb65"}],
#   "channel_name":"Natak","psk_fingerprint":"4baadb65","read_at":1789807215},
#  "busy":false}
```

### `GET /config/op-status`
State of the current/last read or apply job. Poll this after `read`/`apply`.
`status` is one of `idle`, `running`, `done`, `error`. On `error`, `error`
holds the message; on a completed read/apply, `config` holds the fresh config.
```bash
curl -s http://localhost:8080/api/v1/meshtastic/config/op-status
# {"op":null,"status":"idle","error":null,"config":null,
#  "started_at":null,"finished_at":null}
```

### `POST /config/read`
Start reading live config **from the radio** into the cache. Returns
immediately; poll `op-status`. `404` if no radio, `409` if busy.
```bash
curl -s -X POST http://localhost:8080/api/v1/meshtastic/config/read
# {"success":true,"started":true}
```

### `POST /config/apply`
Start writing changed fields to the radio. Body: `{"changes": {...}}`, where
`changes` contains only the fields you want to change. Returns immediately;
poll `op-status`. Validation failures return `400` before the radio is touched.

Allowed fields in `changes`:

| Field | Type | Constraints |
|-------|------|-------------|
| `owner` | string | 1–39 chars (long name) |
| `owner_short` | string | 1–4 chars (short name) |
| `region` | string | one of: `US`, `EU_433`, `EU_868`, `CN`, `JP`, `ANZ`, `KR`, `TW`, `RU`, `IN`, `NZ_865`, `TH`, `LORA_24`, `UA_433`, `UA_868`, `MY_433`, `MY_919`, `SG_923`, `PH_433`, `PH_868`, `PH_915`, `ANZ_433`, `KZ_433`, `KZ_863`, `NP_865`, `BR_902` |
| `modem_preset` | string | one of: `LONG_FAST`, `LONG_SLOW`, `LONG_MODERATE`, `MEDIUM_FAST`, `MEDIUM_SLOW`, `SHORT_FAST`, `SHORT_SLOW`, `SHORT_TURBO` |
| `role` | string | one of: `CLIENT`, `CLIENT_MUTE`, `CLIENT_HIDDEN`, `TRACKER`, `TAK`, `TAK_TRACKER`, `SENSOR`, `ROUTER`, `ROUTER_CLIENT`, `ROUTER_LATE`, `REPEATER`, `LOST_AND_FOUND` |
| `hop_limit` | int | 1–7 |
| `tx_power` | int | 0–30 (dBm) |
| `frequency_slot` | int | 0–104 (`0` = auto, derived from channel name) |
| `channel_name` | string | 1–11 chars |
| `psk` | string | `random`, `default`, `none`, or an explicit key (`base64:...` / hex / `simpleN`); must not be empty |
| `psk_random` | bool | `true`/`false` |

Any field outside this set returns `400 Unknown fields: ...`.
```bash
curl -s -X POST http://localhost:8080/api/v1/meshtastic/config/apply \
  -H 'Content-Type: application/json' \
  -d '{"changes":{"role":"TAK","modem_preset":"SHORT_FAST","tx_power":30}}'
# {"success":true,"started":true}   # then poll /config/op-status
```

### `POST /config/channel-url`
Set channels from a Meshtastic channel URL. Body: `{"url": "https://..."}`.

### `GET /nodes`
LoRa nodes heard over RF recently.
```bash
curl -s http://localhost:8080/api/v1/meshtastic/nodes
# {"nodes":[{"id":"!67c599e4","short_name":"0046","long_name":"0046-nucleus",
#   "last_heard":1789858312,"snr":8.75,"hops_away":0}, ...],"bridge_running":true}
```

### `GET /peers`
Known IP-linked peers (for bridge peering).

### `POST /config/join-peer`
Peer with another node's bridge. Body: `{"host": "10.20.1.46"}`.

### `GET /config/qr`
Channel QR code as an **SVG image** (`image/svg+xml`, not JSON).
```bash
curl -s http://localhost:8080/api/v1/meshtastic/config/qr -o channel.svg
```

---

## Text messaging (`/api/v1/messaging`)

Thin layer over the messaging daemon (WiFi multicast + LoRa). Returns `503`
if that daemon is down.

### `GET /status`
```bash
curl -s http://localhost:8080/api/v1/messaging/status
# {"ok":true,"sender":"0042-nucleus","wifi_group":"239.10.10.60",
#  "wifi_port":17020,"lora":true,"count":12}
```

### `GET /messages?since=<ts>`
Message history. `since` is a Unix timestamp (float); `0` = everything.
```bash
curl -s 'http://localhost:8080/api/v1/messaging/messages?since=0'
# {"ok":true,"messages":[{"id":"59e1...","sender":"0042-nucleus",
#   "text":"Test message","ts":1789832852.6285,"transports":["wifi"],"mine":true}, ...]}
```

### `POST /messages`
Send a message on all transports. Body: `{"text": "..."}`. `400` on failure.
```bash
curl -s -X POST http://localhost:8080/api/v1/messaging/messages \
  -H 'Content-Type: application/json' -d '{"text":"hello mesh"}'
```

### `WS /ws`
WebSocket for live messages. On connect it sends one
`{"event":"history","messages":[...]}`, then a `{"event":"message",...}` for
each new message. Use this instead of polling `/messages` in a UI.

---

## Voice (`/api/v1/voice`)

Status/channel control for the voice daemon. Audio itself does **not** go
through here — it stays on the daemon's own WebSocket at `/voice-ws`. Returns
`503` if the voice daemon is down.

### `GET /status`
```bash
curl -s http://localhost:8080/api/v1/voice/status
# {"ok":true,"node_id":42,"channel":1,"channel_label":"Command",
#  "group":"239.10.10.1","ptt":false,"transport":"ip", ...}
```

### `GET /channels`
```bash
curl -s http://localhost:8080/api/v1/voice/channels
# {"ok":true,"current":1,"channels":[{"n":1,"label":"Command"}]}
```

### `POST /channel`
Switch channel. Body: `{"n": <int>}`. `400` on a bad channel number.
```bash
curl -s -X POST http://localhost:8080/api/v1/voice/channel \
  -H 'Content-Type: application/json' -d '{"n":1}'
```

---

## Web UI routes (not API)

- `GET /` — main UI (`index.html`)
- `GET /voice` — voice UI page (`voice.html`)
- everything else under `/` — static assets

---

## Non-HTTP interfaces (backend, for reference)

The HTTP routers above are thin relays over these. You normally use the HTTP
API, not these directly.

- **Messaging daemon** — UDP control socket `127.0.0.1:5562` (JSON commands:
  `status`, `history`, `send`, `subscribe`).
- **Voice daemon** — UDP control socket `127.0.0.1:5556` (text commands:
  `STATUS`, `CHANNELS`, `CHANNEL <n>`); audio on WebSocket `/voice-ws`.
- **meshtasticd** — native Meshtastic API on TCP `:4403`.
- **babeld monitor** — `[::1]:33123`; send `dump` for neighbours/routes.
