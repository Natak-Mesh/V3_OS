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
| MODEM73 Soundmodem | TCPClientInterface, `kiss_framing = True`, `127.0.0.1:8001` | full (default) | modem73 OFDM soundmodem driving an analog VHF/UHF radio through a DigiRig (USB audio + serial PTT). On by default (harmless if modem73 isn't running; rnsd retries). See [modem73 soundmodem](#modem73-soundmodem-analog-radio). |

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

## modem73 soundmodem (analog radio)

Signal chain: `rnsd` → KISS over TCP `127.0.0.1:8001` → `modem73` (OFDM) →
DigiRig (USB audio + CP210x RTS PTT at `/dev/nucleus/modem`) → UV-5R.
`nucleus-modem73.service` runs `/opt/nucleus/bin/nucleus-modem73-up.sh`, rendered
from the `reticulum.modem73_*` keys in `config.yaml` by `nucleusctl apply`.

### Required settings

| Setting | Value | Why |
|---------|-------|-----|
| `reticulum.modem73_tx_level` (config.yaml) | `5` | At `100`, OFDM's high peak-to-average ratio overdrives the UV-5R mic input / deviation limiter. Frames arrive but distorted (SNR ≈3.7 dB, BER 6–10%). Per node; the schema default is still 100. |
| `--csma-mode sync` (template, fixed) | `sync` | Radios run squelch-off, so receiver hiss is always present. The default `threshold` mode marks the channel busy above −30 dBFS, so hiss holds it permanently BUSY and the node never transmits. `sync` counts only a detected modem73 signal as busy. |
| `--csma-band vhf` (template, fixed) | `vhf` | Selects the VHF/UHF CSMA timing profile instead of modem73's `hf` default. Currently has **no effect**: the band only supplies values through `--csma-preset`, which isn't passed. The `CSMA:` line in `/var/log/modem73.log` still shows slot=500 ms, cw=8, burst=2, dither=250 ms. |
| `reticulum.modem73_mode` | `bpsk_1_2` | Bench-tested mode (788 bps). Must match on all nodes. |

Radio setup (manual, both nodes):

- Squelch **0 (off)**. This avoids squelch-open latency truncating the frame
  start.
- Set the volume knob so the modem73 TUI shows **Carrier ≈ −20 to −30 dB** with
  the channel idle. At volume 0 no audio reaches the DigiRig and RX is dead.
- The knob is the only setting that can't be controlled from software. Set it
  once and leave it.

### Bench results (OFDM BPSK 1/2, `--csma-mode sync`)

Measured in the TUI from the RX node's Last SNR / BER, restarting the RX modem73
to clear stats before each step.

| Direction | Change | SNR | BER |
|-----------|--------|-----|-----|
| 0054 → 0042 | tx-level 50, RX Carrier −4.8 dB | 5.8 dB | 4.83% |
| 0054 → 0042 | RX volume set to Carrier −20..−30 dB | unchanged | unchanged |
| 0054 → 0042 | tx-level 20 | 7.0 dB | 2.59% |
| 0054 → 0042 | tx-level 10 | 11.7 dB | 0.51% |
| 0054 → 0042 | tx-level 5 | 12.7 dB | 0.42% |
| 0042 → 0054 | tx-level 5 | 12.6 dB | 0.56% |

SNR rose as TX level fell, which confirms TX overdrive. Changing RX level had
no effect, which rules out RX clipping. Going from 10 to 5 gained only +1 dB,
so overdrive is mostly resolved at 5.

### Running the modem73 TUI

Use the TUI to test the radio path without Reticulum (test pattern, SNR/BER
readout, carrier and BUSY state).

```bash
# 1. Stop the service. It holds KISS port 8001 and the audio device.
sudo systemctl stop nucleus-modem73

# 2. Launch the TUI: the service's flags without --headless.
modem73 --callsign N0CALL --device "USB Audio Device, USB Audio" --tx-level 5 --modem ofdm --modulation BPSK --rate 1/2 --ptt com --com-port /dev/nucleus/modem --com-line rts --csma-mode sync --csma-band vhf

# 3. Quit with q, then restart the service.
sudo systemctl start nucleus-modem73
```

The TUI flags must match the service. Compare with
`/opt/nucleus/bin/nucleus-modem73-up.sh`, or with `pgrep -a modem73` while the
service is running. rnsd reconnects to port 8001 automatically, so anything
it sends while the TUI is up goes out over the radio.

Bench test: run the TUI on both nodes, send a test pattern from one, and read
Last SNR / BER on the other. Restart the RX TUI to clear stats between runs.

### Verifying with Reticulum

- `rnstatus`: `TCPInterface[MODEM73 Soundmodem/127.0.0.1:8001]` should be `Up` at
  788 bps. Its ↑/↓ counters prove RNS frames crossed the radio.
- `rnpath <destination_hash>` / `rnpath -t`: shows the interface a path uses.
  RNS keeps **one** path per destination. If the nodes also peer over eth0 or
  the Wi-Fi mesh, the path shows the faster interface, because the slower
  modem73 copy of the announce is dropped as a duplicate. Use the counters,
  or remove the other interfaces, to prove the radio path.
- `tail -f /var/log/modem73.log`: `TX: <n> bytes` lines on the sender, decode
  lines on the receiver.

### Gain adjustment (untested, for later)

The DigiRig's ALSA controls (card `Device`) adjust levels from software:

```bash
amixer -c Device scontents                    # current levels
amixer -c Device sset Mic <0-35> capture      # RX capture gain (0042: 20 = +8 dB)
amixer -c Device sset Speaker <0-37>          # TX playback level (0042: 17 = −20 dB)
```

- **`Mic` capture** scales audio after the radio, so it moves modem73's Carrier
  reading. It can bring Carrier back to −20..−30 dB after a bumped volume knob,
  as long as the knob isn't at zero.
- **`Speaker` playback** reduces TX drive below the `--tx-level` floor of 5. The
  step from 10 to 5 still gained +1 dB, so lowering it a little further may
  add a bit more SNR.
- Neither is managed by nucleusd or persisted across reboots
  (`alsactl store` would persist them). Change one at a time and re-measure
  SNR/BER in the TUI.

### Troubleshooting

- `Port 8001 is already in use`: another modem73 is running (the service, or a
  manual instance). Run `pgrep -a modem73`. Use `sudo kill <pid>` if it's
  root-owned.
- Constant **BUSY** in the TUI: squelch-off hiss with `threshold` CSMA. Confirm
  `--csma-mode sync` is in the flags.
- Low SNR / high BER: lower the TX level first, then check RX Carrier.

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
- **Interfaces:** each configured interface with its mode (full/internal/…),
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
