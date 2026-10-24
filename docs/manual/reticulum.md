# Reticulum

Every node runs `rnsd` (Reticulum Network Stack daemon) as a transport node.
The config is rendered by `nucleusctl apply` from `/etc/nucleus/config.yaml`
(`reticulum:` block) to `~natak/.reticulum/config` — never hand-edit it.

## Interfaces

| Interface | Type | Mode | Purpose |
|-----------|------|------|---------|
| Mesh AutoInterface | AutoInterface on `wlan1`, `eth0` (+ `halow0` if present) | internal | Auto-peers with other nodes over the 802.11s mesh and over a direct wired/fiber `eth0` link (IPv6 link-local + multicast). In eth0 `lan` mode the network config enables a router-less IPv6 link-local (`LinkLocalAddressing=ipv6`, `IPv6AcceptRA=no`) so the multicast route installs with no router present; its UDP ports (29716/29717/42671) are opened by the host firewall. |
| LAN TCP Server | TCPServerInterface on `br-lan:4242` | internal | Attach point for client apps (Sideband, MeshChat, nomadnet) on the node's AP/LAN. |
| Entry Node | TCPClientInterface | boundary | Optional uplink to a public-IP Reticulum node joining the wider network. |
| KISS TNC | KISSInterface (serial) | — | Optional packet radio TNC, off by default. |
| RNode LoRa | RNodeInterface on `/dev/nucleus/rnode` | — | USB LoRa RNode radio, on by default (harmless with no radio plugged in). Only the RAK4631 is supported. |

### Announce propagation (boundary/internal modes)

The entry-node uplink runs in `boundary` mode and local interfaces in
`internal` mode. Announces arriving from the public network are **not**
flooded onto the mesh or to LAN clients; announces from the mesh/LAN still
propagate out through the entry node, so local destinations stay reachable
from outside. Local clients can still reach outside destinations on demand —
internal-mode interfaces resolve paths across the boundary via recursive path
requests. This must be consistent on all nodes (it is: the modes ship in the
rendered template), otherwise public announces leak in via any node missing it.

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
**RETICULUM → INTERFACES → CONFIGURE RNODE** (enable/disable, frequency,
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
rate; fire an announce (Reticulum page → **Announce now**) and watch TX/RX
counters grow on both nodes.

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

The web UI has a **RETICULUM** page (main menu), refreshed every 5 s, that is
the single hub for the subsystem:

- **Status:** rnsd up/down, uptime and the total number of known paths.
- **My identity:** this node's own messaging address ("share this") and the
  announce mode, with an **Announce now** button.
- **Interfaces:** each configured interface with its mode (internal/boundary/…),
  up/down state, RX/TX totals and attached-client count. The internal shared-
  instance interface is hidden (it is not a real transport). A **CONFIGURE
  RNODE** sub-page edits the RNode LoRa radio settings (see above).
- **Links:** `DIRECT MESSAGES` (the saved contacts / conversations) and
  `ADD CONTACTS` (my card / mesh / link).

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

- **By destination hash (the normal way):** on the **Add Contacts** page, paste
  the peer's lxmf.delivery address and an optional nickname, then **Add contact**.
  Entirely out-of-band — the hash can be dictated, written down or read off
  another screen, so the peer need not be on the mesh or reachable at all. The
  contact is stored keyless and **nothing is transmitted**; it shows "no key yet"
  until the key is learned, which happens when you press **req path** (a path
  response carries the key) or the peer messages you. This works for any LXMF
  client, Nucleus or not.
- **Over the WiFi mesh (Nucleus-to-Nucleus convenience):** the Add Contacts page
  lists Nucleus nodes found via Babel routes (same discovery as Meshtastic
  channel sharing) and offers a one-tap **Add** that pulls the peer's card over
  the local HTTP API and verifies its address↔key. Because that carries the key,
  a mesh-added contact is immediately messageable. This is an internal pull, not
  a shareable format.

This node's own address to hand out — its lxmf.delivery destination hash — is in
the **Share me** section of the Add Contacts page (Reticulum → Add Contacts).
Direct Messages holds only the saved contacts and conversations.

Contacts are saved to `/var/lib/nucleus/rns/contacts.json`. Once a contact's
public key is known (pulled over the mesh, carried in a path response after
**req path**, or captured from an inbound message) it is persisted and loaded
into RNS at daemon start, so `rnpath`/delivery resolve on demand and the contact
stays messageable across restarts. An inbound message from an unknown sender
auto-adds that sender as a contact.

**Naming.** Hashes aren't human-readable, so each contact carries a local
**nickname** (set when adding by hash, or later via **Rename** on the **Modify
contacts** page) and an **announced name** (the peer's LXMF display name, learned
with the key). The UI shows the nickname if set, otherwise the announced name,
otherwise a short hash. **Remove** (with a confirm prompt) forgets a contact
locally. Both live on the **Modify contacts** page (reached from the button at
the bottom of the Direct Messages page), not in the per-contact message log.

Each contact row on the Direct Messages page shows that contact's current path
state inline — `path <hops>h, <age>` when rnsd knows a route (age is how long ago
the path was recorded), or `no path` otherwise — with an inline **req path**
button that emits a single path request for just that contact. Reading the state
emits nothing; only the button does. Tap the rest of the row to open the
conversation.

Announcing is **manual by default** (`messaging.rns.announce_mode: manual`):
nothing is broadcast until you press **Announce now** on the main Reticulum page
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
