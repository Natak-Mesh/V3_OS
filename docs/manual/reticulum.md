# Reticulum

Every node runs `rnsd` (Reticulum Network Stack daemon) as a transport node.
The config is rendered by `nucleusctl apply` from `/etc/nucleus/config.yaml`
(`reticulum:` block) to `~natak/.reticulum/config` — never hand-edit it.

## Interfaces

| Interface | Type | Mode | Purpose |
|-----------|------|------|---------|
| Mesh AutoInterface | AutoInterface on `wlan1` | internal | Auto-peers with other nodes over the 802.11s mesh (IPv6 link-local). |
| LAN TCP Server | TCPServerInterface on `br-lan:4242` | internal | Attach point for client apps (Sideband, MeshChat, nomadnet) on the node's AP/LAN. |
| Entry Node | TCPClientInterface | boundary | Optional uplink to a public-IP Reticulum node joining the wider network. |
| KISS TNC | KISSInterface (serial) | — | Optional packet radio TNC, off by default. |

### Announce propagation (boundary/internal modes)

The entry-node uplink runs in `boundary` mode and local interfaces in
`internal` mode. Announces arriving from the public network are **not**
flooded onto the mesh or to LAN clients; announces from the mesh/LAN still
propagate out through the entry node, so local destinations stay reachable
from outside. Local clients can still reach outside destinations on demand —
internal-mode interfaces resolve paths across the boundary via recursive path
requests. This must be consistent on all nodes (it is: the modes ship in the
rendered template), otherwise public announces leak in via any node missing it.

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

## Troubleshooting

- `rnstatus` — interface state, connected clients, traffic counters.
- `rnpath -t` — path table (which announces this node knows, and via what).
- Client can't connect: confirm the device got a `10.20.<id>.x` DHCP lease,
  the host/port are exact, and the app was hard-restarted.
- Service: `systemctl status rnsd`, logs via `journalctl -u rnsd`.
