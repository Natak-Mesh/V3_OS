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

## Config (`/etc/nucleus/config.yaml`, `messaging:` section)

`enabled`, `wifi_group`, `wifi_port`, `lora`, `dedupe_window_secs`,
`history_limit`. Validated by `nucleusd.schema.MessagingConfig`.

## Limitations

- No delivery receipts/threading beyond stock Meshtastic.
- Messages from Meshtastic-only radios arrive via LoRa only (never on the WiFi
  lane), so nucleus nodes out of RF range won't see them.
