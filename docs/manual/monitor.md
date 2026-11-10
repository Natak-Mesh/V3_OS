# Monitor page (MESH CONNECTIONS)

Who this node can currently reach. Two tables (three when a HaLow radio is
plugged in), auto-refreshed every 5s. Diagnostic detail (interface addresses,
service states) lives on the [System](system.md) page.

## Radio-off alerts

A radio that has been deliberately switched off looks exactly like a broken
mesh from this page (empty tables). So when a radio is off, amber alerts are
shown at the top of the page, followed by a selectable
**» OPEN RADIOS (RF ON/OFF)** link to the [Radios](radios.md) page where it is
switched back on:

| Alert | Shown when |
|-------|------------|
| Wi-Fi mesh radio is OFF | `mesh.enabled` is `false` (Radios page switch off). |
| Wi-Fi mesh is enabled but the wlan1 radio is still switched off | Config says on, but the live radio disagrees (`radios.mesh.rf_mismatch`) — apply the config or reboot. |
| Meshtastic is receive-only | `meshtastic.tx_enabled` is `false` — LoRa nodes are still heard, but this node does not transmit. |
| HaLow radio is plugged in but turned off | The HaLow radio is present but `halow.enabled` is `false`. |

The access point is not listed: it is live-only and always returns at boot. If
the radio state can't be read, no alert is shown (no false alarms). The header's
`mesh` indicator also reads **`RF OFF`** (amber) instead of `DOWN` while the
Wi-Fi mesh is switched off.

## Wifi mesh nodes

Every node reachable over the wlan1 wifi mesh (Babel routing). Only
Babel-selected (installed) routes to node br-lan /24 prefixes are shown; backup
routes and gateway/self prefixes are omitted. Empty means this node sees no other
node over wlan1. If the Wi-Fi mesh is switched off, the table is replaced by
"Wi-Fi mesh is turned off" instead (see Radio-off alerts above).

| Column | Meaning |
|--------|---------|
| Node | Mesh IPv4 of the node (10.20.1.\<id\>). |
| Via | `direct` if it is a 1-hop neighbour, otherwise the mesh IPv4 of the next hop used to reach it. |
| Cost | Path cost, normalized so a perfect direct link = 1.0. Each additional perfect hop adds 1.0 (perfect 1-hop path = 2.0). Any excess above the whole number is packet loss on the path. Derived from Babel's route metric (raw metric / 256). |

Notes:
- Cost reflects link quality, not a true hop count — Babel does not report hop count, only cumulative metric. A lossy link inflates the cost above the ideal whole number.

## HaLow mesh nodes

Shown **only when a HaLow (802.11ah) radio is plugged in** (the `halow0`
interface is present). This is the HaLow 802.11s radio link itself — the peers
`halow0` is associated with — not the Babel route table. That distinction
matters: when the same node is also reachable over wlan1 at a lower cost, Babel
installs the route over wlan1 and the node would never appear as a HaLow peer in
the Wifi mesh table, even though the HaLow link is up. This table reads the radio
directly so HaLow adjacencies are always visible.

| Column | Meaning |
|--------|---------|
| Node | The peer's mesh IPv4 (10.20.1.\<id\>) when Babel has resolved it, otherwise the peer's radio MAC. |
| Link | Babel link quality on halow0 as a percentage (recent hellos received), or `—` before Babel forms the adjacency. |
| Signal | Average received signal in dBm from the radio (`iw`), or `—`. |
| State | 802.11s peer-link state (`ESTAB` when fully linked, green; anything else amber). |
| Seen | Time since the last frame from this peer (from the radio's inactivity timer). |

Empty means the radio is present but sees no other node over halow0. HaLow
channel/bandwidth and on/off live on the [HaLow Mesh](halow.md) page (the `HALOW MESH`
menu entry, also shown only when the radio is plugged in).

## LoRa nodes

Meshtastic nodes heard over RF, from the CoT bridge. If cot-bridge is not
running, LoRa visibility is unavailable. Nodes not heard in the last 15 min are
dropped. When Meshtastic is receive-only, a note under the table says so.

| Column | Meaning |
|--------|---------|
| Node | Long name, else short name, else node id. |
| Heard | Time since last heard (green if within 5 min, else amber). |
| SNR | Signal-to-noise ratio of the last packet, or `—`. |
| Hops | Hops away, or `—`. |


## Link test

**» LINK TEST (ping / throughput)** at the bottom of the page opens on-demand
tests to one peer. Nothing runs until a test button is pressed; the last result
stays on the page.

| Link | Target | Tests |
|------|--------|-------|
| `wifi` | Mesh node (from the tables above) | **PING** (5 ICMP packets over wlan1), **THROUGHPUT SEND** / **RECEIVE** (5 s iperf3, this node → peer / peer → this node). |
| `halow` | Mesh node | Same tests over halow0 (10.30.1.\<id\>). Fails with "No such device" if this node has no HaLow radio. |
| `lora` | Meshtastic node | **TRACEROUTE**: hops, per-hop SNR and round-trip time. The reply can take tens of seconds; the page updates when it arrives. |
| `reticulum` | Reticulum contact | **PROBE**: 3 `rnprobe` packets. Shows loss, average RTT, hops, the interface Reticulum used (RNode LoRa, mesh, eth0) and, over the RNode, RSSI/SNR/link quality. Up to ~40 s. |

Notes:
- Both ends of a Wi-Fi/HaLow test are bound to that radio, so the result is for
  that radio even if Babel routes the node over the other one.
- iperf3 is not a service. For each throughput test this node asks the peer to
  start a one-shot iperf3 server that exits after the test (or after 20 s if the
  test never connects). The peer must be on a version with link tests, otherwise
  the result says "does not support link tests (update it)".
- One test at a time per node. Throughput tests saturate the radio for 5 s, which
  delays voice and other mesh traffic in the meantime.
- Reticulum picks its own path: if the peer is also reachable over eth0 or the
  mesh, the probe reports that interface rather than the RNode.
