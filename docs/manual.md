# Nucleus Manual

## Monitor page

Shows every node currently reachable over the wifi mesh (Babel routing).

| Column | Meaning |
|--------|---------|
| Node | Mesh IPv4 of the node (10.20.1.\<id\>). |
| Via | `direct` if it is a 1-hop neighbour, otherwise the mesh IPv4 of the next hop used to reach it. |
| Cost | Path cost, normalized so a perfect direct link = 1.0. Each additional perfect hop adds 1.0 (perfect 1-hop path = 2.0). Any excess above the whole number is packet loss on the path. Derived from Babel's route metric (raw metric / 256). |

Notes:
- Cost reflects link quality, not a true hop count — Babel does not report hop count, only cumulative metric. A lossy link inflates the cost above the ideal whole number.
- Only Babel-selected (installed) routes to node br-lan /24 prefixes are shown; backup routes and gateway/self prefixes are omitted.
