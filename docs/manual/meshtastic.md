# Meshtastic Radio page

Configure the local Meshtastic LoRa radio and align its channel with peers.

## Status

A status line shows: radio `detected` / `not detected`, CoT bridge
`running` / `stopped`, and whether the bridge is `enabled` / `disabled` at boot.

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

- **Heartbeat** — `on` / `off`.
- **Interval (s)** — 60–3600 (60s steps).
- **Save heartbeat** — active within one bridge cycle (~10s).
