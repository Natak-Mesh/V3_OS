# Text Messaging — one store, two parallel transports

Text messaging on a Nucleus node delivers over **two independent transports in
parallel**, both landing in a **single message store** that the web UI reads:

```
WiFi UDP mcast ─┐
                ├─► MessageStore (dedupe) ─► /api/v1/messaging ─► chat page
LoRa (portnum 1)┘         ▲
 via cot_bridge relay     │
                          └─ send(text): fan out to BOTH transports
```

## Transports

- **WiFi** — UDP multicast on the wlan1 802.11s mesh (default `239.10.10.60:17020`).
  Fast, free, multi-hop at L2. Wire frame is a tiny JSON line `{sender,text,ts}`;
  only Nucleus nodes speak it.
- **LoRa** — **standard Meshtastic `TEXT_MESSAGE_APP` (portnum 1)** on the
  primary channel, relayed through the CoT bridge (which owns the radio). Because
  it is ordinary Meshtastic text with **no custom header**, plain Meshtastic
  radios/phones on the same channel interoperate: they see Nucleus messages and
  their messages appear in the Nucleus chat.

## Single store + de-duplication

Every inbound message — from either transport, including Meshtastic-only radios —
is ingested into one `MessageStore`. The WiFi and LoRa copies of the same
utterance collapse into **one entry** tagged with both transports. Since the LoRa
copy carries no id (interop constraint), messages are matched by
`(sender, text)` within `dedupe_window_secs` (default 60s). The UI shows a
per-message badge of the transport(s) that delivered it (`wifi`, `lora`, or
`wifi+lora`).

## Components

- `nucleusd/messaging/store.py` — pure, unit-tested store + dedupe.
- `nucleusd/messaging/service.py` — daemon (`nucleus-messaging.service`): both
  transports + a UDP control socket (127.0.0.1:5562).
- `nucleusd/messaging/router.py` — FastAPI layer at `/api/v1/messaging`.
- Web: MESSAGING page in the CLI/TUI shell.
- CoT bridge text relay: localhost UDP 5560 (TX) / 5561 (RX).

## Third lane — Reticulum/LXMF direct messages (opt-in)

Unlike WiFi and LoRa, which broadcast into the one deduped store, the Reticulum
lane sends **addressed, one-to-one** LXMF messages to a specific node, so it has
its own **per-peer conversation store** (`rns_store.ConversationStore`,
persisted to `/var/lib/nucleus/rns_messages.jsonl`) rather than the shared log.
It is **off by default** (`messaging.rns.enabled`); the RNS/LXMF libraries
(~27 MB) are imported only when it is on.

```
                             nucleus.node announce ─► PeerTable (discovery)
rnsd (shared instance) ◄──── lxmf.delivery  ───────► ConversationStore ─► /api/v1/messaging/rns/*
   ▲ client, not a 2nd stack        (LXMRouter)              ▲
   └─ RnsLane attaches as a client of the already-running rnsd
```

- **Identity** — one NODE identity at `/var/lib/nucleus/rns/identity` (created
  once, 0600, survives redeploys). Not tied to any phone/user. Both announced
  destinations derive from it.
- **Two announces from that one identity:**
  - `lxmf.delivery` — standard LXMF inbox (display name = hostname), so Sideband,
    MeshChat, NomadNet and other nodes can message this node.
  - `nucleus.node` (app `nucleus`, aspect `node`) — our own announce. app_data is
    a compact msgpack map (`{v,id,host,mesh_ip,br_lan,sw,caps}`, ≤300 B) derived
    by `NucleusConfig.rns_announce_appdata`. A peer that hears it recognises a
    Nucleus node and computes that node's `lxmf.delivery` address from the same
    identity — that is how nodes discover each other and know where to send.
- **Never a second stack** — `RnsLane.start()` first probes rnsd
  (`nucleusd.reticulum._rpc`) and only calls `RNS.Reticulum()` once rnsd answers,
  then with `require_shared_instance=True`. If rnsd is down the lane defers and
  retries every 30 s; the WiFi/LoRa lanes are unaffected.
- **Components:** `rns_proto.py` (pure wire format + `PeerTable`, unit-tested
  off-box), `rns_lane.py` (live RNS/LXMF wiring), `rns_store.py` (per-peer store).
- **API:** `GET /api/v1/messaging/rns/{status,peers,messages}`,
  `POST /api/v1/messaging/rns/messages {dest,text}`; discovered nodes also at
  `GET /api/v1/reticulum/nodes`. Live inbound DMs push over the existing WS as
  `{"event":"rns_message"}`.

## Config (`/etc/nucleus/config.yaml`, `messaging:` section)

`enabled`, `wifi_group`, `wifi_port`, `lora`, `dedupe_window_secs`,
`history_limit`, and `rns:` (`enabled`, `announce_interval_secs`,
`propagation_node`). Validated by `nucleusd.schema.MessagingConfig` /
`RnsMessagingConfig`.

## Limitations

- No delivery receipts/threading beyond stock Meshtastic.
- Messages from Meshtastic-only radios arrive via LoRa only (never on the WiFi
  lane), so nucleus nodes out of RF range won't see them.
- Reticulum lane: direct messages only (no group chat); an outbound send whose
  path isn't known yet requests the path and must be retried shortly. An LXMF
  propagation node (store-and-forward for offline peers) is opt-in
  (`messaging.rns.propagation_node`) and costs extra memory/disk.
