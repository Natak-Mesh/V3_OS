# Nucleus Manual

Operator manual for the Nucleus web UI — one page per UI page (see Pages below).

## Overview

A Nucleus node is a Raspberry Pi radio node. Nodes self-form an off-grid network
and carry text messaging and PTT voice across it, and interoperate with plain
Meshtastic / ATAK radios over LoRa. Operated from any phone/laptop on the node's
AP via this web UI (or the same REST API / `nucleusctl`).

## Architecture

```
 RADIOS                     DAEMONS                        API / UI
 wlan1 802.11s ── mesh ──►  babeld (L3) + smcroute ─┐
   2.4GHz backbone          nucleus-mesh            │
 LoRa (Meshtastic) ──────►  cot-bridge ─────────────┤
   long-range interop       (radio owner, relays)   ├─► nucleusd ─► web UI
 wlan0 AP 5GHz ─┐                                    │   (FastAPI     /api/v1
 eth0 wan/lan  ─┴─ operator access                   │    :8080)      + nginx :80/443
                            nucleus-messaging ───────┤
                            nucleus-voice ───────────┘
```

### Radios

| Radio | Role | Characteristics | UI page |
|-------|------|-----------------|---------|
| wlan1 802.11s (2.4GHz) | Mesh backbone; babeld L3 routing, smcroute multicast, HWMP off | High bandwidth, multi-hop, node-to-node | [Monitor](monitor.md) |
| LoRa (Meshtastic) | Long-range interop; owned by cot-bridge | Low bandwidth, long range, plain-Meshtastic/ATAK compatible | [Meshtastic](meshtastic.md) |
| wlan0 AP (5GHz) + eth0 | Operator/client access (AP, WAN or LAN) | Local — how operators reach the node | [Config](config.md) |

### Services

| Daemon | Function | UI page | Internals |
|--------|----------|---------|-----------|
| nucleus-mesh / babeld / smcroute | Bring up + route the 802.11s mesh | [Monitor](monitor.md) | README §1 |
| cot-bridge | Owns the LoRa radio; ATAK CoT bridge, message + heartbeat relay | [Meshtastic](meshtastic.md) | — |
| nucleus-messaging | WiFi mcast + LoRa → one deduped message store | [Messaging](messaging.md) | [messaging-internals.md](messaging-internals.md) |
| nucleus-voice | UDP-mcast PTT voice; optional LoRa STT/TTS | [Voice](voice.md) | [voice-internals.md](voice-internals.md) |
| nucleusd | Always-on FastAPI: config/apply/status, REST API, serves this UI | [System](system.md) | [API.md](../API.md) |
| takserver *(optional)* | Official TAK Server (tak.gov) + MediaMTX; off by default, provisioned per-node | — | [takserver.md](takserver.md) |

### Config pipeline

One file drives everything: `config.yaml → schema (derive) → render templates →
write-if-changed → restart affected units`. The web UI, REST API and
`nucleusctl` are the same operations against the same schema. Edit and apply from
[Config](config.md); update code from [Update](update.md). Full
detail in the [README](../README.md) and [API.md](../API.md).

## The shell

Every page (except the standalone `/voice` handset) runs inside one CLI/TUI
shell served at `/`.

- **Header** (fixed, top) — always shows:
  - `node` — this node's hostname.
  - `ip` — mesh IPv4 on wlan1 (`10.20.1.<id>`).
  - `mesh` — `N connected` (Babel neighbours, green), `0 connected` (amber), or
    `DOWN` (nucleus-mesh not active). Refreshed every 5s.
- **View** (scrolls) — the current page's content.
- **Control bar** (fixed, bottom), identical on every page:
  - `▲` / `▼` — move the cursor between selectable items (also ↑/↓ keys).
  - `ENTER` — activate the item / enter edit mode (Enter key).
  - `BACK` — go back one page (Esc key).

Selectable item types: navigation links, action buttons, and editable fields
(text, number stepper, and cycle-through option pickers). Display-only blocks
(tables, hints) are skipped by the cursor.

## Pages

| Menu item | Page |
|-----------|------|
| MESH CONNECTIONS | [Monitor](monitor.md) |
| MESSAGING | [Messaging](messaging.md) |
| VOICE (PTT) | [Voice](voice.md) |
| MESHTASTIC | [Meshtastic Radio](meshtastic.md) |
| INTERFACES AND SERVICES | [System](system.md) |
| RADIO CONFIGURATION | [Config](config.md) |
| TAK SERVER *(optional)* | [TAK Server](takserver.md) |
| SYSTEM UPDATE | [Update](update.md) |

Optional per-node subsystems (not web UI pages): [TAK Server](takserver.md).

For subsystem internals see [messaging-internals.md](messaging-internals.md), [voice-internals.md](voice-internals.md)
and [API.md](../API.md).
