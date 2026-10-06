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
  contact card (mesh/link/QR) ─► ContactStore ─► Identity.remember (RNS)
rnsd (shared instance) ◄──── lxmf.delivery  ───────► ConversationStore ─► /api/v1/messaging/rns/*
   ▲ client, not a 2nd stack        (LXMRouter)              ▲
   └─ RnsLane attaches as a client of the already-running rnsd
```

- **Identity** — one NODE identity at `/var/lib/nucleus/rns/identity` (created
  once, 0600, survives redeploys). Not tied to any phone/user. The lxmf.delivery
  destination and this node's shareable contact card derive from it.
- **Contact cards replace the old custom announce.** There is no `nucleus.node`
  announce any more. A card is `{v,name,id,lxmf_hash,pubkey}`, encoded as a
  `nucleus-rns://<base64url msgpack>` link (also rendered as a QR). On import the
  importer recomputes `lxmf_hash` from `pubkey` and **rejects any card whose two
  halves disagree** (`rns_proto.verify_card`) — a card can't claim an address it
  has no key for. The address derivation (`lxmf_delivery_hash`) reproduces
  `RNS.Destination.hash(identity,"lxmf","delivery")` purely and is pinned to the
  real library by a fixture test.
- **No announce needed to send.** On start — and whenever a contact is imported —
  the lane calls `RNS.Identity.remember(pubkey)` for every stored contact, so
  `RNS.Identity.recall()` returns a usable identity and sends resolve a path on
  demand. The only thing ever announced is the standard `lxmf.delivery`
  destination, and only **manually** (operator "Announce now" button, or once on
  the first outbound send) unless `announce_mode: auto`.
- **Discovery over the WiFi mesh** reuses the shared `nucleusd.mesh_peers` helper
  (Babel next-hops + per-peer HTTP), the same path as Meshtastic channel sharing:
  `GET /rns/mesh-peers` pulls each neighbour's `/rns/card`.
- **Unknown senders are auto-added** as contacts (`ContactStore.add_inbound`): if
  a node has our address it got our card on purpose. The sender's public key is
  captured from the message/`Identity.recall` when available (reply-ready), else
  the contact is keyless until the key is learned.
- **Never a second stack** — `RnsLane.start()` first probes rnsd
  (`nucleusd.reticulum._rpc`) and only calls `RNS.Reticulum()` once rnsd answers,
  then with `require_shared_instance=True`. If rnsd is down the lane defers and
  retries every 30 s; the WiFi/LoRa lanes are unaffected.
- **Components:** `rns_proto.py` (pure card wire format + address derivation,
  unit-tested off-box), `rns_contacts.py` (persistent `ContactStore`),
  `rns_lane.py` (live RNS/LXMF wiring), `rns_store.py` (per-peer message store),
  `mesh_peers.py` (shared Babel/HTTP discovery).
- **API:** `GET /api/v1/messaging/rns/{status,contacts,messages,card,card/qr,mesh-peers}`,
  `POST /rns/contacts {link|card,source}`, `DELETE /rns/contacts/{hash}`,
  `POST /rns/announce`, `POST /rns/messages {dest,text}`. Live inbound DMs push
  over the existing WS as `{"event":"rns_message"}`.

## Config (`/etc/nucleus/config.yaml`, `messaging:` section)

`enabled`, `wifi_group`, `wifi_port`, `lora`, `dedupe_window_secs`,
`history_limit`, and `rns:` (`enabled`, `announce_mode` [`manual`/`auto`],
`announce_interval_secs`, `propagation_node`). Validated by
`nucleusd.schema.MessagingConfig` / `RnsMessagingConfig`.

## Limitations

- No delivery receipts/threading beyond stock Meshtastic.
- Messages from Meshtastic-only radios arrive via LoRa only (never on the WiFi
  lane), so nucleus nodes out of RF range won't see them.
- Reticulum lane: direct messages only (no group chat). You can only message a
  node whose contact card you've imported (or that has messaged you); there is no
  passive announce-based discovery. A send to a contact whose routing path isn't
  cached yet requests the path and retries via LXMF. An LXMF propagation node
  (store-and-forward for offline peers) is opt-in
  (`messaging.rns.propagation_node`) and costs extra memory/disk.
