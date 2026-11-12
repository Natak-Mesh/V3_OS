# Nucleus V3 OS — HTTP API

Reference for every HTTP endpoint the node serves. Current version: **0.8.0**.

## API use 

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

### `GET /api/v1/config/schema`
The config's JSON schema — every field's type, bounds, enum options and
description, straight from `schema.py`. Clients (e.g. the web UI's RNode page)
read field limits/options from here instead of hardcoding a second copy that
could drift from the schema. Read-only; no config I/O.
```bash
curl -s http://localhost:8080/api/v1/config/schema
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

`wifi_peers` / `halow_peers` are the **direct** 802.11s peers on one radio each
(`wlan1` / `halow0`): Babel neighbours on that interface joined to
`iw dev <iface> station dump`. Same fields for both — `ipv4`, `ipv6`, `mac`,
`link_pct`, `cost`, `signal_avg_dbm`, `plink`, `inactive_ms`, `connected_s`
(radio fields `null` when the station can't be joined). `link_pct` is the share
of the neighbour's last 16 Babel hellos received (Babel `reach` bits set / 16;
~64 s window at the 4 s hello interval). A node linked over both
radios appears in both lists; a link on one radio never appears in the other's.

`mesh_nodes` lists every mesh node (direct or multi-hop, over any radio — see
`iface`) seen in the last 15 min. `reachable` is whether Babel has a route to it right now; `last_seen`
is the epoch time it last did. A lost node keeps its last route (`via`,
`metric`, `direct`) until it ages out. Babel takes ~1 min to notice a dead
node. If babeld itself is down, every node is reported lost. History is
in-memory and resets when nucleusd restarts.

`babel_neighbours` is one entry per Babel *link*, so a node reached over several
radios (e.g. wlan1 + halow0) appears more than once. `neighbour_nodes` collapses
these to one entry per distinct node (grouped by mesh IPv4), listing the
interfaces it is reached over — this is what the web UI header counts, so the
count reflects nodes, not links.
```bash
curl -s http://localhost:8080/api/v1/status
# {"interfaces":{"wlan1":{...}}, "services":{"babeld":"active",...},
#  "babel_neighbours":[{"ipv4":"10.20.1.46","link_pct":94,...}],
#  "neighbour_nodes":[{"node":"10.20.1.52","ifaces":["halow0","wlan1"]}],
#  "mesh_nodes":[{"node":"10.20.1.46","via":null,"metric":256,"direct":true,
#    "reachable":true,"last_seen":1790531600.2}], ...}
```

`radios` gives per-radio on/off state for the [Radios](manual/radios.md) page —
the config source-of-truth combined with live rfkill/unit state. `on` is the
operator-facing state; `rf_blocked` is the live rfkill soft-block (`null` =
unknown). `mesh.rf_mismatch` is `true` when the live wlan1 radio disagrees with
`mesh.enabled` (e.g. enabled in config but still rfkill-blocked — apply or
reboot), `false` when they agree, `null` if either is unknown; the MESH
CONNECTIONS page alerts on it. `mesh` and `meshtastic` are `persistent`
(config-backed); `ap` is live only and resets ON at boot. All best-effort —
`radios` is `null` if it can't be read.
```bash
curl -s http://localhost:8080/api/v1/status | jq .radios
# {"mesh":{"on":true,"rf_blocked":false,"rf_mismatch":false,"persistent":true},
#  "meshtastic":{"on":true,"receive_only":false,"daemon":"active","persistent":true},
#  "ap":{"on":true,"rf_blocked":false,"hostapd":"active","persistent":false}}
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

### `POST /api/v1/power/reboot`
Reboot the node. Returns before the node goes down.
```bash
curl -s -X POST http://localhost:8080/api/v1/power/reboot
# {"started":true}
```

### `POST /api/v1/power/poweroff`
Power off the node. Returns before the node goes down; a power off needs physical
access to bring the node back.
```bash
curl -s -X POST http://localhost:8080/api/v1/power/poweroff
# {"started":true}
```

### `POST /api/v1/rf/ap`
Live on/off for the Wi-Fi access point (wlan0) — rfkill phy0 + hostapd
stop/start + re-enslave wlan0 into br-lan. The AP is **outside the config
pipeline** and always resets to ON at boot, so this is a live-only toggle (there
is no `config.yaml` key). Body: `{"on": <bool>}`.
```bash
curl -s -X POST http://localhost:8080/api/v1/rf/ap \
  -H 'Content-Type: application/json' -d '{"on":false}'
# {"ok":true}
```

The **mesh** (wlan1) and **Meshtastic TX** radios are config-backed instead:
toggle them with `PUT /api/v1/config` (`mesh.enabled` / `meshtastic.tx_enabled`)
+ `POST /api/v1/apply`. Live per-radio state is in `GET /api/v1/status` under
`radios` (below). See the [Radios](manual/radios.md) manual page.


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

### `POST /heartbeat`
Broadcast a presence heartbeat now. Relayed to the CoT bridge (which owns the
radio); works even when the periodic heartbeat is disabled and resets the
periodic timer. `404` if the bridge isn't running or doesn't respond; `400` if
the bridge reports the send failed.
```bash
curl -s -X POST http://localhost:8080/api/v1/meshtastic/heartbeat
# {"success":true,"sent":true}
```

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

### Meshtastic client (`/client...`)
Chats/nodes for using the radio as a Meshtastic node. Reads the CoT bridge's
`/run` chat store (cleared on boot); radio actions are relayed to the bridge
(UDP 127.0.0.1:5564). `404` if the bridge isn't running · `400` bad input or
receive-only. Chat keys: `ch<N>` = channel N, `!<hex>` = DM with that node.

```bash
curl -s http://localhost:8080/api/v1/meshtastic/client
# {"status":{"bridge_running":true,"my_id":"!3007771f","my_name":"0042-nucleus",
#   "tx_enabled":true,"online":3,"channel_util":3.5,"air_util_tx":0.4,...},
#  "chats":[{"chat":"ch0","title":"Natak","last_ts":1791650050.0,
#    "last_text":"ok","unread":2}, ...]}
curl -s 'http://localhost:8080/api/v1/meshtastic/client/messages?chat=ch0'
# {"chat":"ch0","messages":[{"pkt_id":1234568,"sender":567591628,"from":"0052",
#   "text":"hello","ts":1791650001.0,"mine":0,"reply_id":null,"state":null,
#   "reactions":[{"sender":805795615,"emoji":"👍","from":"me"}]}]}   # marks read
curl -s -X POST http://localhost:8080/api/v1/meshtastic/client/messages \
  -H 'Content-Type: application/json' -d '{"chat":"!21d4c2cc","text":"hi","reply_id":null}'
curl -s -X POST http://localhost:8080/api/v1/meshtastic/client/react \
  -H 'Content-Type: application/json' -d '{"chat":"ch0","reply_id":1234568,"emoji":"👍"}'
curl -s http://localhost:8080/api/v1/meshtastic/client/nodes
# {"me":{...},"nodes":[{"id":"!21d4c2cc","long_name":"0052-nucleus","hw_model":"...",
#   "role":"TAK","last_heard":...,"snr":8.75,"hops_away":0,"position":{"lat":..,"lon":..},
#   "battery":90,"distance_m":1112,"bearing":0,"favorite":false,"ignored":false,
#   "traceroute":{"ts":..,"route":{"towards":[..],"snr_towards":[..],"back":[..],"snr_back":[..]}}}]}
curl -s -X POST http://localhost:8080/api/v1/meshtastic/client/nodes/!21d4c2cc/traceroute
#   also: request_position, request_telemetry (replies update /client/nodes)
curl -s -X PUT http://localhost:8080/api/v1/meshtastic/client/nodes/!21d4c2cc/favorite \
  -H 'Content-Type: application/json' -d '{"on":true}'          # also /ignored
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

### `GET /name` / `PUT /name`
This node's name: the WiFi/LoRa chat sender and the Reticulum (LXMF) announce
display name. `GET` → `{"ok":true,"name":"Alpha","display_name":"Alpha"|null,
"hostname":"0042-nucleus"}`. `PUT` body `{"name":"..."}` (`""` resets to the
hostname) saves `messaging.display_name` to `config.yaml` and updates the running
daemon (`"live":true`; `false` if the daemon is down — it picks the name up on
start). `422` on an invalid name (control characters, >32 chars). Transmits
nothing; peers learn the new LXMF name on the next announce.
```bash
curl -s -X PUT http://localhost:8080/api/v1/messaging/name \
  -H 'Content-Type: application/json' -d '{"name":"Alpha"}'
```

### `WS /ws`
WebSocket for live messages. On connect it sends one
`{"event":"history","messages":[...]}`, then a `{"event":"message",...}` for
each new message. Inbound Reticulum direct messages (below) push on the same
socket as `{"event":"rns_message","message":{...}}`. Use this instead of polling.

### Reticulum direct messages (`/api/v1/messaging/rns/...`)
Per-contact one-to-one LXMF messages over `rnsd`, distinct from the broadcast
WiFi/LoRa log above. Off unless `messaging.rns.enabled`; endpoints then return
empty/`disabled` cleanly rather than erroring.

Contacts are **explicit** — there is no flooded announce. A contact is identified
by its **lxmf.delivery destination hash** (the standard Reticulum/LXMF address,
32 hex chars). The normal way to add one is by that hash alone — keyless and
out-of-band, transmitting nothing. Nucleus nodes can also be pulled over the WiFi
mesh (an internal card that carries the key, verified address↔key). An inbound
message also auto-adds its sender. The public key — needed to actually send — is
learned later (mesh pull, operator **req path**, or a signed inbound message) and
persisted.

- `GET /rns/status` — `{"enabled":bool,"started":bool,"address":"<lxmf hash>",
  "name":"<node name>","announce_mode":"manual"}`. `address` is this node's lxmf.delivery destination
  hash — the thing to share.
- `GET /rns/contacts` — known contacts:
  `{"ok":true,"contacts":[{"lxmf_hash":"...","pubkey":"...","name":"","
  "announced_name":"0009-nucleus","id":9,"source":"hash","added":<ts>,
  "last_seen":<ts|null>}]}`. `source` is the provenance (`hash`/`mesh`/`inbound`).
  `name` is the local nickname; `announced_name` is the peer's LXMF display name.
  `pubkey` is `""` until the key is learned.
- `POST /rns/contacts` — add a contact. Body `{"dest":"<lxmf hash>","name":"<opt
  nickname>"}` (the normal case) **or** `{"card":{...}}` from a mesh pull, plus a
  `source` tag. Add-by-hash validates the 32-hex shape and transmits nothing; a
  card is verified address↔key. `400` on a bad hash or a key/address mismatch.
  ```bash
  curl -s -X POST http://localhost:8080/api/v1/messaging/rns/contacts \
    -H 'Content-Type: application/json' \
    -d '{"dest":"<lxmf hash>","name":"Alice","source":"hash"}'
  ```
- `PATCH /rns/contacts/{dest}` — set/clear the local nickname. Body `{"name":
  "..."}` (`""` clears). Transmits nothing.
- `DELETE /rns/contacts/{dest}` — forget a contact by its lxmf hash.
- `GET /rns/card` — this node's contact card (`{"ok":true,"card":{...}}`) for a
  neighbouring Nucleus node to pull over the mesh. Not a shareable off-node
  format — hand out the destination hash from `/rns/status` instead. `503` until
  the lane is started.
- `GET /rns/mesh-peers` — Nucleus nodes reachable over the WiFi mesh, each with
  its contact card for one-tap add:
  `{"ok":true,"peers":[{"ip":"10.20.1.9","card":{...}}]}`. Found via Babel
  neighbours; nodes with the lane off are omitted. Cards are untrusted until
  added (which re-verifies address↔key).
- `POST /rns/announce` — emit this node's `lxmf.delivery` announce once
  (operator-triggered): `{"ok":true,"announced":bool}`.
- `GET /rns/contacts/{dest}/path` — read-only path state for a contact:
  `{"ok":true,"path":{"known":bool,"hops":<n|null>,"interface":"<name>|null"}}`.
  Reads rnsd's local path table only — **emits no radio traffic**. Pair `known`
  with the contact's `last_seen` for path age.
- `POST /rns/contacts/{dest}/path` — operator-triggered path discovery: emit
  **one** path request for the contact over every RNS interface; does not retry.
  Returns `{"ok":true,"requested":true,"path":{...}}`. With announces off this is
  the only way a contact becomes reachable. `400` if the lane is down.
- `GET /rns/latest` — newest message per peer, for contact-list previews:
  `{"ok":true,"latest":{"<hash>":{"peer":...,"text":...,"ts":...,"direction":"in"|"out","state":...}}}`.
- `GET /rns/messages?peer=<hash>&since=<ts>` — conversation history (omit `peer`
  for all peers merged).
- `POST /rns/messages` — send a DM. Body `{"dest":"<lxmf hash>","text":"..."}`.
  A send **never** announces or requests a path on its own (LPI). It `400`s with
  `no path to contact — request a path first` when no path is known — request one
  via `POST /rns/contacts/{dest}/path`, then send.
```bash
curl -s -X POST http://localhost:8080/api/v1/messaging/rns/messages \
  -H 'Content-Type: application/json' -d '{"dest":"<hex>","text":"hi node"}'
```

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

## Reticulum (`/api/v1/reticulum`)

Read-only status for the Reticulum daemon (`rnsd`). nucleusd talks to the shared
`rnsd` instance over its local control socket (the same one `rnstatus`/`rnpath`
use) — no second Reticulum stack is started, so this adds negligible memory.
`running` is `false` (not an error) when rnsd is down; genuine socket/parse
failures return `503`.

### `GET /status`
Transport identity + uptime + totals, the configured interfaces, and the path
count. Per-peer/per-client spawned interfaces are omitted (as in `rnstatus`).
```bash
curl -s http://localhost:8080/api/v1/reticulum/status
# {"running":true,
#  "transport":{"transport_id":"5fdaeff7...","uptime":2220.5,
#    "rxb":385411,"txb":777338,"rxs":0.0,"txs":0.0},
#  "interfaces":[{"name":"AutoInterface[Mesh AutoInterface]",
#    "short_name":"Mesh AutoInterface","up":true,"mode":"full",
#    "bitrate":10000000,"rxb":0,"txb":0,"rxs":0.0,"txs":0.0,
#    "announces_in":0.0,"announces_out":0.0,"clients":null}, ...],
#  "path_count":2810}
```

### `GET /interfaces`
Just the interface list from `/status` (same per-interface shape).
```bash
curl -s http://localhost:8080/api/v1/reticulum/interfaces
# {"interfaces":[{...}]}
```

### `GET /paths`
The full path table: for each known destination its next-hop (`via`), `hops`,
the `interface` it was learned on, and `timestamp`/`expires` epochs.
```bash
curl -s http://localhost:8080/api/v1/reticulum/paths
# {"paths":[{"hash":"3c736576...","via":"4dc77e9d...","hops":4,
#   "interface":"TCPInterface[Entry Node/173.230.150.24:4243]",
#   "timestamp":1791121039.2,"expires":1791725839.2}, ...]}
```

---

## Link tests (`/api/v1/linktest`)

On-demand connectivity/throughput tests from this node to one peer. Nothing runs
until requested: iperf3 is never a service — the peer spawns a one-shot
`iperf3 -s -1` (killed by `timeout` after `secs + 15` s) for each test. One test
at a time per node: `409` if busy · `400` peer is this node · `503` tool/peer
failure (detail says why) · `422` bad input. `link` is `wifi` (wlan1,
10.20.1.<id>) or `halow` (halow0, 10.30.1.<id>); both ends are bound to that
radio, so the result is for that radio even when Babel routes the node via the
other.

### `POST /ping`
```bash
curl -s -X POST http://localhost:8080/api/v1/linktest/ping \
  -H 'Content-Type: application/json' -d '{"link":"halow","peer_id":47,"count":5}'
# {"link":"halow","dev":"halow0","peer_ip":"10.30.1.47","sent":5,"received":5,
#  "loss_pct":0.0,"rtt_ms":{"min":4.7,"avg":5.0,"max":5.7,"mdev":0.4}}
```

### `POST /iperf`
TCP throughput for `secs` (1-30, default 5). `reverse:false` = this node → peer,
`true` = peer → this node. `mbps` is goodput at the receiving end. Both nodes
must run a version with this endpoint.
```bash
curl -s -X POST http://localhost:8080/api/v1/linktest/iperf \
  -H 'Content-Type: application/json' -d '{"link":"wifi","peer_id":47,"secs":5,"reverse":false}'
# {"link":"wifi","dev":"wlan1","peer_ip":"10.20.1.47","direction":"send",
#  "mbps":22.09,"mbps_sender":22.43,"bytes":13893632,"seconds":5.03,"retransmits":12}
```

### `POST /iperf-server`
Peer side of `/iperf`, called node-to-node by the testing node. Accepted only
from the wlan1/halow0 subnets or localhost (`403` otherwise).

### `POST /rnprobe`
`rnprobe` (as the rnsd user, through the shared instance) to a contact's
`lxmf.delivery` hash; Reticulum picks the interface (RNode LoRa, mesh, eth0).
`probes` 1-3, `timeout` 1-10 s per probe. `error` is set when no path was found.
```bash
curl -s -X POST http://localhost:8080/api/v1/linktest/rnprobe \
  -H 'Content-Type: application/json' -d '{"dest":"fa65075c2a28f00976d5542e7c87ebfa"}'
# {"dest":"fa65...","sent":3,"received":3,"loss_pct":0.0,"rtt_ms":[5.2,4.8,7.7],
#  "rtt_avg_ms":5.9,"hops":1,"interface":"AutoInterfacePeer[eth0/fe80::...]",
#  "rssi_dbm":null,"snr_db":null,"quality_pct":null,"error":null}
```
RSSI/SNR/link quality are filled in when the reply came over the RNode.

Meshtastic LoRa uses the existing traceroute
(`POST /api/v1/meshtastic/client/nodes/{id}/traceroute`); its result in
`/client/nodes` now carries `rtt_s` (request → reply round trip).

---

## modem73 live (`/api/v1/modem73`)

Live link tuning for the modem73 VHF/UHF soundmodem, relayed over modem73's
local control port (`127.0.0.1:8073`, never exposed directly). One shared
connection serves all clients; it is held only while someone polls or has the
WebSocket open. Live changes (`tx_level`, `mixer`) take effect at once and last
until modem73 restarts; `save` persists. Errors: `503` modem73 / amixer
unavailable · `502` modem73 rejected the command · `422` bad input.

### `GET /status`
Everything the modem73 page shows. `frames` = last 10 decoded frames, newest first
(modem73 `rx_frame` fields: `callsign`, `time`, `level_db` RX audio dBFS, `snr`
dB, `ber_pct` pre-FEC, `mode`, ...). Frames with an empty `callsign` are noise
decodes. `live` = what the running modem uses (`mode` is `null` if it is not a
registry mode); `saved` = config.yaml (levels, mode, `enabled`, `callsign`). `attempts` = last 10 frames heard (callsign-less noise syncs dropped),
from `/var/log/modem73.log`, incl. CRC failures (the control port reports
decodes only), newest first: `time` (null if read at startup), `callsign`,
`modulation`, `code_rate`, `ok` (true / false = CRC fail / null = in
progress), `snr` dB (decoded SNR; mean per-symbol SNR on CRC fail), `ber_pct`
(decoded only).
```bash
curl -s http://localhost:8080/api/v1/modem73/status
# {"online":true,"error":null,"callsign":"0042",
#  "status":{"channel_state":"idle","ptt_on":false,"rx_frame_count":3,"crc_errors":0,...},
#  "status_age_s":1.2,
#  "live":{"mode":"qpsk_1_2","modulation":"QPSK","code_rate":"1/2","payload_size":512,
#          "tx_level":5,"rx_gain":15,"tx_gain":11},
#  "saved":{"mode":"qpsk_1_2","tx_level":5,"rx_gain":15,"tx_gain":11},
#  "frames":[{"seq":3,"time":1794349203.45,"callsign":"0047","level_db":-12.8,
#             "snr":4.97,"ber_pct":6.6,"mode":"QPSK 1/2 N",...}],
#  "attempts":[{"time":1794349203.4,"callsign":"0047","modulation":"QAM256",
#               "code_rate":"3/4","ok":false,"snr":16.1,"ber_pct":null}],
#  "modes":{"rdm_1200":{"family":"robust","bitrate":1150,"payload":512,...},...}}
```

### `WS /ws`
Pushes `{"event":"modem73","view":<same as GET /status>}` on connect and on
every change (new frame, status poll, config change).

### `POST /test`
Transmits one short test frame now (keys the radio): payload
`M73:<callsign>:nucleus test <n>`, modem73's text-message format. The far node
sees its level/SNR/BER; rnsd discards it.
```bash
curl -s -X POST http://localhost:8080/api/v1/modem73/test
# {"ok":true,"seq":1,"bytes":26}
```

### `POST /clear`
Empties `frames` and `attempts` (in-memory only; `/var/log/modem73.log` is
kept). Frames decoded before the clear don't come back on reconnect.
```bash
curl -s -X POST http://localhost:8080/api/v1/modem73/clear
# {"ok":true}
```

### `PUT /tx_level`
Live modem73 TX level, 5-100 % (`tx_drive` = level/100). Not saved.
```bash
curl -s -X PUT http://localhost:8080/api/v1/modem73/tx_level \
  -H 'Content-Type: application/json' -d '{"tx_level":10}'
```

### `PUT /mixer`
Live sound-card gains (raw ALSA steps): `rx_gain` 0-35 (`Mic Capture Volume`),
`tx_gain` 0-37 (`Speaker Playback Volume`). Either or both. Not saved.

### `POST /save`
Writes any of `mode`, `tx_level`, `rx_gain`, `tx_gain`, `enabled`,
`callsign` (`""` = node id) to config.yaml (other keys: `422`), then
applies: modem73 restarts (and rnsd too when the mode, hence the RNS bitrate,
changed). Mode changes are save-only so the RNS bitrate always matches.
```bash
curl -s -X POST http://localhost:8080/api/v1/modem73/save \
  -H 'Content-Type: application/json' -d '{"mode":"rdm_1200","tx_level":5}'
# {"ok":true,"changed":["/opt/nucleus/bin/nucleus-modem73-up.sh",...],
#  "units_restarted":["rnsd","nucleus-modem73"]}
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
