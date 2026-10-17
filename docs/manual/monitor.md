# Monitor page (MESH CONNECTIONS)

Who this node can currently reach. Two tables (three when a HaLow radio is
plugged in), auto-refreshed every 5s. Diagnostic detail (interface addresses,
service states) lives on the [System](system.md) page.

## Wifi mesh nodes

Every node reachable over the wlan1 wifi mesh (Babel routing). Only
Babel-selected (installed) routes to node br-lan /24 prefixes are shown; backup
routes and gateway/self prefixes are omitted. Empty means this node sees no other
node over wlan1.

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
dropped.

| Column | Meaning |
|--------|---------|
| Node | Long name, else short name, else node id. |
| Heard | Time since last heard (green if within 5 min, else amber). |
| SNR | Signal-to-noise ratio of the last packet, or `—`. |
| Hops | Hops away, or `—`. |

