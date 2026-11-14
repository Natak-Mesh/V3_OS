# Meshtastic Radio page

Configure the local Meshtastic LoRa radio and align its channel with peers, and
use it as a regular Meshtastic node (CHATS / NODES / POSITIONS).

## Meshtastic client

For use without ATAK — what the Meshtastic phone app does, minus radio config
(that stays on the pages below / `config.yaml`). Everything goes through the CoT
bridge, which stays the radio's only client, so CoT, voice, heartbeat and
messaging are unaffected. Requires the CoT bridge to be running; sending is
refused while the radio is receive-only.

- **CHATS** — status strip (this node, nodes online in the last 2 h, channel
  utilisation, airtime TX) and one entry per channel (`#`) and DM (`@`), with
  `● n` unread counts. In a chat:
  - type + **Send**; DMs show delivery state (`sending` / `delivered` / `failed`).
  - **» REPLY / REACT** — pick a message: Enter replies to it, **react** sends an
    emoji reaction. Replies show `↪ sender: text`; reactions show under the message.
- **NODES** — every node in the radio's node DB. **Filter** (name or `!id`),
  **Sort** (heard / name / hops / snr / distance), **Show** (all / favorites /
  ignored). Favorites are listed first. Open a node for hardware, role, battery,
  channel util, uptime, position, distance/bearing and:
  **DIRECT MESSAGE**, **TRACEROUTE** (result shown on the page with per-hop SNR),
  **REQUEST POSITION**, **REQUEST TELEMETRY**, **FAVORITE**, **IGNORE**
  (hides the node's messages and DM chat).
- **POSITIONS** — distance and bearing from this node to every node with a
  position, nearest first. Needs this node's own position.

Chat history is RAM-only (`/run/nucleus-meshchat/`, max 500 messages per chat):
kept across bridge restarts, **cleared on every boot**. Favorites, ignored nodes
and read marks persist in `/var/lib/nucleus/meshclient.json`.

Plain primary-channel broadcasts also appear in [Messaging](messaging.md); DMs,
secondary channels and reactions appear only here.

## Status

A status line shows: radio `detected` / `not detected`, CoT bridge
`running` / `stopped`, and whether the bridge is `enabled` / `disabled` at boot.

> **Transmit on/off:** the Meshtastic radio can be put in **receive-only** mode
> (`meshtastic.tx_enabled: false`) from the [Radios](radios.md) page — it keeps
> receiving but never transmits. This is separate from the channel/radio
> configuration below.

A colour legend applies to the config fields below:
- **Blue (accent)** — channel identity; must match across nodes to communicate.
- **Amber (warn)** — this node only; does not affect interoperability.

## Radio configuration (collapsible)

Toggle **Radio configuration** to expand. **Read config from radio** loads the
current settings into the editable fields:

| Field | Notes |
|-------|-------|
| Long name | Owner long name (max 39). |
| Short name | Owner short name (max 4). |
| Modem preset | LONG_FAST … SHORT_TURBO. |
| Freq slot (0=auto) | 0–104. |
| Channel name | Blue — channel identity (max 11). |
| Encryption key | Blue — `keep` / `random` / `default` / `none`. |
| Region | Blue — regulatory region. |
| Hop limit | Amber — 1–7. |
| TX power (dBm) | Amber — 0–30. |
| Role | Amber — CLIENT … REPEATER. |

- **Apply to radio** — writes the changes; the radio reboots (~30–120s).
  Changing the encryption key prompts for confirmation (peers can't communicate
  until they receive the new channel URL).
- **Channel URL** field + **Import channel URL** — apply a pasted channel URL.
- **Show QR code** — renders the current channel URL as a QR for another radio
  to scan.

## Join a peer's Meshtastic channel

**Query Nucleus peers for Meshtastic channels** asks other Nucleus devices over
the wifi mesh for their Meshtastic configs and compares each to this node's:

| Column | Meaning |
|--------|---------|
| Nucleus IP | Peer's mesh IP (or `unreachable / no Meshtastic config`). |
| Channel / Key / Region / Preset / Slot | Peer value, green if it matches this node, else amber. |
| Match | `match`, `differs`, or a **Join** button. |

**Join** copies the peer's channel name, key, region, preset and frequency slot
to this radio (role, TX power and hop limit are untouched); the radio reboots.

## Presence heartbeat

Node config (not radio) — read live by the CoT bridge, so no radio reboot. Keeps
this node in peers' LoRa list without ATAK traffic; nodes drop off after 15 min
of silence.

The heartbeat carries this node's short and long names, so a peer heard only via
heartbeats shows its real names instead of its hardware id (`!xxxxxxxx`) without
waiting for an infrequent Meshtastic NODEINFO broadcast. Heartbeats from older
nodes (names absent) still list the sender by hardware id.

- **Heartbeat** — `on` / `off`.
- **Interval (s)** — 60–3600 (60s steps).
- **Save heartbeat** — active within one bridge cycle (~10s).
- **Send heartbeat now** — broadcasts one heartbeat immediately, even when the
  periodic heartbeat is off. Requires the CoT bridge to be running. This also
  restarts the interval timer, so the next automatic heartbeat is a full
  interval away.

## TX rate limit

Node config (not radio) — read live by the CoT bridge, so no radio reboot.
Minimum seconds between LoRa transmissions of the same CoT UID; drops repeat
updates for a UID inside the window to keep the airwaves clear.

- **TX rate limit (s)** — 0–3600. `0` disables the limit.
- **Save TX rate limit** — active within one bridge cycle (~10s).

## CoT TX source filter

The CoT bridge transmits ATAK multicast on LoRa only when its source is this
node's br-lan subnet, so Wi-Fi mesh traffic other nodes already carry is not
retransmitted. With `eth0.mcast_mode: mesh_relay`, the br-lan subnets of the
RF-silent nodes in `eth0.mcast_source_node_ids` are accepted too, except their
`.1` gateway address (their own cot-bridge's LoRa injections, never re-sent).
Read live, ~10s. See [eth0 mesh relay](eth0-mesh-relay.md).
