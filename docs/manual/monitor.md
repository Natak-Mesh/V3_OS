# Monitor page (MESH CONNECTIONS)

Who this node can currently reach. Two tables, auto-refreshed every 5s.
Diagnostic detail (interface addresses, service states) lives on the
[System](system.md) page.

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

