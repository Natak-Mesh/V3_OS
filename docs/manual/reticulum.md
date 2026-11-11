# Reticulum

Every node runs `rnsd` (Reticulum Network Stack daemon) as a transport node.
The config is rendered by `nucleusctl apply` from `/etc/nucleus/config.yaml`
(`reticulum:` block) to `~natak/.reticulum/config` — never hand-edit it.

## Interfaces

| Interface | Type | Mode | Purpose |
|-----------|------|------|---------|
| Mesh AutoInterface | AutoInterface on `wlan1`, `eth0` (+ `halow0` if present) | full | Auto-peers with other nodes over the 802.11s mesh and over a direct wired/fiber `eth0` link (IPv6 link-local + multicast). In eth0 `lan` mode the network config enables a router-less IPv6 link-local (`LinkLocalAddressing=ipv6`, `IPv6AcceptRA=no`) so the multicast route installs with no router present; its UDP ports (29716/29717/42671) are opened by the host firewall. |
| LAN TCP Server | TCPServerInterface on `br-lan:4242` | internal | Attach point for client apps (Sideband, MeshChat, nomadnet) on the node's AP/LAN. |
| RNode LoRa | RNodeInterface on `/dev/nucleus/rnode` | full (default) | USB LoRa RNode radio, on by default (harmless with no radio plugged in). Only the RAK4631 is supported. |
| MODEM73 Soundmodem | TCPClientInterface, `kiss_framing = True`, `127.0.0.1:8001` | full (default) | modem73 OFDM soundmodem driving an analog VHF/UHF radio through a DigiRig (USB audio + serial PTT). On by default (harmless if modem73 isn't running; rnsd retries). See [modem73](modem73.md). |

There is no public-IP uplink: the Entry Node interface is disabled in the
template (`reticulum-config.j2`), so nodes peer only over the mesh, eth0 and
the radios.

### Interface modes

- **Mesh AutoInterface — `full`.** eth0/wlan1/halow0 peers are real neighbour
  nodes, so announces and path responses must propagate across them. `internal`
  suppresses that propagation and is only correct for co-located
  shared-instance links.
- **LAN TCP Server — `internal`.** Client apps attach here. They can still
  resolve paths on demand via path requests.
- **RNode LoRa / MODEM73 Soundmodem — `full`** (RNS default, no `mode` set).
  Radio links to other nodes, so announces propagate across them.

Modes ship in the rendered template, so they are consistent on every node.

## RNode LoRa radio (USB)

A USB LoRa [RNode](https://unsigned.io/rnode/) gives Reticulum a long-range radio
link independent of the WiFi mesh and the Meshtastic radio. **On by default** —
harmless with no radio plugged in (`rnsd` just retries the port). **Only the
RAK4631** (nRF52840 + SX1262) is supported for now; flash it with RNode firmware
on another machine before plugging it in (`rnodeconf --autoinstall`).

Tune it (or disable it) in the `reticulum:` block:

```yaml
reticulum:
  rnode_enabled: true            # false to disable
  # rnode_frequency: 914875000   # Hz — keep clear of the Meshtastic channel
  # rnode_bandwidth: 250000      # Hz
  # rnode_txpower: 22            # dBm (RAK4631 max 22)
  # rnode_spreadingfactor: 9     # 5-12
  # rnode_codingrate: 5          # 5-8 → 4/5-4/8
```

Then `sudo nucleusctl apply`. The same settings are editable from the web UI at
**RETICULUM → NODE STATUS & INTERFACES → CONFIGURE RNODE** (enable/disable, frequency,
bandwidth, spreading factor, coding rate, TX power); **Save** writes them to
`config.yaml` and **Apply** restarts `rnsd`. The page reads its field limits and
the valid bandwidth list from the config schema, so they always match
`schema.py`. Deeper interface configuration beyond the RNode radio is done by
editing `config.yaml`, not from the web UI.

The radio is bound to the stable path
`/dev/nucleus/rnode` by a generated udev rule (see
[usb-devices.md](usb-devices.md)), so the config never depends on which
`/dev/ttyACM*` the board enumerates as, and an unsupported device plugged into
the same port is never touched by `rnsd`.

**All nodes that should talk over the RNode link must use identical radio
parameters** (frequency, bandwidth, spreading factor, coding rate). The defaults
are a MediumFast-equivalent profile (250 kHz / SF9 / CR4:5, ≈3.5 kbps on air).
Pick a frequency clear of the node's Meshtastic channel.

Verify: `rnstatus` shows `RNodeInterface[RNode LoRa]` as `Up` with an on-air
rate; fire an announce (Reticulum page → **ANNOUNCE**) and watch TX/RX
counters grow on both nodes.

## modem73 soundmodem (analog radio)

rnsd reaches an analog VHF/UHF radio through the modem73 OFDM soundmodem
(KISS over TCP `127.0.0.1:8001`, DigiRig audio + PTT). Settings are the
`reticulum.modem73_*` keys in `config.yaml`. Setup, tuning and bench results:
[modem73](modem73.md).

## Connecting a client device (Sideband etc.)

Devices on a node's AP/LAN do **not** discover the node automatically — add
the node's TCP server as an interface in the app:

1. Connect the device to the node's AP (or wired LAN).
2. In the app, add a **TCPClientInterface**:
   - Host: the node's br-lan IP — `10.20.<node-id>.1` (e.g. node 42 → `10.20.42.1`)
   - Port: `4242`
3. Enable the interface and **fully restart the app** (force-kill and reopen —
   RNS only opens interfaces at init; a settings toggle is not enough).

Verify on the node: `rnstatus` should show the LAN TCP Server with
`Clients: 1+`. Clients attached to different nodes then see each other's
announces across the mesh (transport is enabled on every node).

## Reticulum page

The web UI's **RETICULUM** page (main menu, refreshed every 5 s) opens on your
contacts, NomadNet-style:

- **me: <name> · rename** — this node's name, its lxmf.delivery address (the
  thing to share) and the announce mode. Tap the row to rename the node (see
  [Node name](#node-name)); the inline **ANNOUNCE** button emits one
  `lxmf.delivery` announce.
- **Contacts** — saved contacts only, most recent conversation first. Each row
  shows `●` for an unread incoming message, the path state, and a preview of
  the last message, plus an inline **REQ PATH** button. Tap a row to open the
  conversation.
- **ADD CONTACT** — add by address, or one-tap from Nucleus nodes on the WiFi
  mesh.
- **NETWORK (n heard)** — LXMF announces heard on-air. They are kept off the
  landing page on purpose, so a busy or internet-connected network can't flood
  the contact list.
- **NODE STATUS & INTERFACES** — rnsd up/down, uptime, total paths, and each
  interface with its mode, up/down state, RX/TX totals and client count (the
  internal shared-instance interface is hidden). Links to **CONFIGURE RNODE**
  and **CONFIGURE MODEM73**.

The interface and path data read straight from the running `rnsd` over its local
control socket — the shared instance exposed by `share_instance = Yes` — so
nothing starts a second Reticulum stack. The same data is on the API at
`GET /api/v1/reticulum/status` (and `/interfaces`, `/paths`). If `rnsd` is
stopped the page shows a "not running" state rather than erroring.

## Contacts & direct messaging (LXMF)

With `messaging.rns.enabled`, the messaging daemon attaches to this same shared
`rnsd` as a client (never a second stack) and exposes one persistent node
identity's standard `lxmf.delivery` inbox. There is **no custom flooded
announce** for discovery. A contact is identified by its **lxmf.delivery
destination hash** — the standard Reticulum/LXMF address (32 hex chars) that
every LXMF client (Sideband, MeshChat, NomadNet, other Nucleus nodes) shows and
accepts. Add one by:

- **By destination hash (the normal way):** on the **Add Contact** page, paste
  the peer's lxmf.delivery address and an optional nickname, then **Add contact**.
  Entirely out-of-band — the hash can be dictated, written down or read off
  another screen, so the peer need not be on the mesh or reachable at all. The
  contact is stored keyless and **nothing is transmitted**; it shows "no key yet"
  until the key is learned, which happens when you press **req path** (a path
  response carries the key) or the peer messages you. This works for any LXMF
  client, Nucleus or not.
- **Over the WiFi mesh (Nucleus-to-Nucleus convenience):** the Add Contact page
  lists Nucleus nodes found via Babel routes (same discovery as Meshtastic
  channel sharing) and offers a one-tap **+ add** that pulls the peer's card over
  the local HTTP API and verifies its address↔key. Because that carries the key,
  a mesh-added contact is immediately messageable. This is an internal pull, not
  a shareable format.
- **From the Network page:** tap a heard announce to add it (keyed, since the
  announce carries the key), or **IGNORE** to drop it from the list.

This node's own address to hand out is at the top of the Reticulum page.

Contacts are saved to `/var/lib/nucleus/rns/contacts.json`. Once a contact's
public key is known (pulled over the mesh, carried in a path response after
**req path**, or captured from an inbound message) it is persisted and loaded
into RNS at daemon start, so `rnpath`/delivery resolve on demand and the contact
stays messageable across restarts. An inbound message from an unknown sender
auto-adds that sender as a contact.

**Naming.** Hashes aren't human-readable, so each contact carries a local
**nickname** (set when adding by hash, or later with **Rename**) and an
**announced name** (the peer's LXMF display name, learned with the key). The UI
shows the nickname if set, otherwise the announced name, otherwise a short hash.

**Conversation and contact pages.** A conversation is the message log, then a
contact bar (`<name> · <path> · contact »`, with inline **REQ PATH**) above the
compose box. Opening a conversation marks it read (per browser). Tap the contact
bar for the **Contact** page: full address, nickname, announced name, key and
path state, how it was added, last heard, and the actions **Message**,
**Rename**, **Request path** and **Remove** (asks for confirmation). Rename and
remove only change the local contact list; nothing is transmitted.

Path state (`<iface> <hops>h <age>`, or `no path`) is read from rnsd's local
path table and emits nothing. Only **req path** transmits: one path request
for that contact.

### Node name

One name is used both as this node's sender name in WiFi/LoRa chat and as the
display name in its LXMF announces (what Sideband, MeshChat and NomadNet show).
Set it by tapping **me:** on the Reticulum page, or in `config.yaml`:

```yaml
messaging:
  display_name: Alpha Base   # max 32 chars; blank/absent = hostname
```

A UI rename is saved to `config.yaml` and applied to the running messaging
daemon right away; no restart is needed. Peers only learn the new name from
the next announce, so press **ANNOUNCE** afterwards. If you edit `config.yaml`
by hand, run `sudo systemctl restart nucleus-messaging` to pick it up
(`nucleusctl apply` does not restart the messaging daemon).

Announcing is **manual by default** (`messaging.rns.announce_mode: manual`):
nothing is broadcast until you press **ANNOUNCE** on the Reticulum page
(a send also announces once so the recipient can find a path back). Set
`announce_mode: auto` to re-announce the `lxmf.delivery` destination every
`announce_interval_secs`. See [messaging-internals.md](messaging-internals.md).
Off by default; the RNS/LXMF libraries load only when enabled.

## Troubleshooting

- `rnstatus` — interface state, connected clients, traffic counters.
- `rnpath -t` — path table (which announces this node knows, and via what).
- Client can't connect: confirm the device got a `10.20.<id>.x` DHCP lease,
  the host/port are exact, and the app was hard-restarted.
- Service: `systemctl status rnsd`, logs via `journalctl -u rnsd`.
