# eth0 mesh relay (`eth0.mcast_mode: mesh_relay`)

Carries ATAK multicast from an **RF-silent node** over a wired eth0 link (fiber
media converter or copper) to an **RF relay node**, which forwards it onto its
radio meshes (wlan1 802.11s, halow0 802.11ah) and Meshtastic LoRa. Traffic in
the reverse direction (mesh/LoRa → RF-silent node) uses the same path.

Unicast across the link is unaffected: Babel routes it over eth0 in every mode.

## Topology

```
 ATAK EUDs                                              radio meshes
 10.20.A.0/24                                           wlan1 / halow0 / LoRa
     │                                                       ▲
  br-lan ── Node A ── eth0 ══ fiber ══ eth0 ── Node B ───────┘
           RF-silent                          RF relay
           mcast_mode: br_lan (default)       mcast_mode: mesh_relay
           no radio TX                        mcast_source_node_ids: [A]
```

| Node | `eth0.mcast_mode` | Radios | Config change |
|------|-------------------|--------|---------------|
| A (RF-silent) | `br_lan` (default) | off | `mesh.enabled: false`, `halow.enabled: false`, `meshtastic.tx_enabled: false` |
| B (RF relay) | `mesh_relay` | on | `mcast_mode: mesh_relay`, `mcast_source_node_ids: [<A's id>]` |

Both nodes need `eth0.babel: true` (default). The schema rejects `mesh_relay`
without it.

## Modes

| `eth0.mcast_mode` | smcroute routes involving eth0 | Default |
|-------------------|--------------------------------|---------|
| `off` | none | |
| `br_lan` | eth0 → br-lan, br-lan → eth0 | yes |
| `mesh_relay` | `br_lan` routes + eth0 → wlan1, wlan1 → eth0, and eth0 → halow0, halow0 → eth0 when `halow.enabled` | |

The routes cover every ATAK group: 239.2.3.1 (CoT/SA), 224.10.10.1
(discovery/GeoChat), 239.255.255.1/.2/.12 (voice). Every route is one-way,
never back out the ingress interface (no-echo). wlan1 ↔ halow0 is never
routed in any mode.

The legacy key `eth0.mcast_bridge: true|false` is read as `br_lan|off`, and
apply rewrites it to `mcast_mode`.

## Setup

Both nodes need a config change. Apply on each node after editing.

### Node A (RF-silent)

Turn off all radio TX (RADIOS page, or `config.yaml`):

```yaml
mesh:
  enabled: false
halow:
  enabled: false
meshtastic:
  tx_enabled: false
```

Leave `eth0.mcast_mode` at `br_lan` (default).

### Node B (RF relay)

Web UI → RADIO CONFIGURATION → eth0:

1. **ATAK mcast mode** → `mesh_relay`
2. **Relay source node IDs** → A's node ID: the number in A's hostname
   (`0017-nucleus` → `17`). Comma-separate multiple IDs.
3. **Save** → **Dry-run apply** (expect `/etc/smcroute.conf` and
   `nucleus-firewall.sh` changed) → **Apply**.

Or `config.yaml`, then `sudo nucleusctl apply`:

```yaml
eth0:
  mcast_mode: mesh_relay
  mcast_source_node_ids: [17]
```

Without A's ID in **Relay source node IDs**, A's CoT is forwarded to wlan1 and
halow0 but **not transmitted on LoRa**.

### Disable

Set B's **ATAK mcast mode** back to `br_lan` and apply. `mcast_source_node_ids`
is kept but has no effect outside `mesh_relay`.

## Forwarding paths

### A's EUD → radio meshes + LoRa

1. An EUD on A's br-lan sends to 239.2.3.1 with TTL 1.
2. A's nftables (`ip nucleus_mcast`) raises the TTL to `mesh.mcast_ttl` (8) on
   br-lan ingress.
3. A's smcroute forwards br-lan → eth0 (TTL 7).
4. B receives the packet on eth0. B's TTL bump on eth0 only matches TTL 1, so
   the packet keeps TTL 7.
5. B's smcroute forwards eth0 → br-lan, eth0 → wlan1 and eth0 → halow0
   (TTL 6). 802.11s carries it multi-hop (RMC dedup, `mesh_ttl` limit).
6. B's `cot-bridge` receives it on br-lan. The source is in `10.20.A.0/24`
   (from `mcast_source_node_ids`), so it passes the multicast → LoRa source
   filter and is compressed and sent over LoRa, subject to
   `meshtastic.tx_enabled` and the per-UID rate limit.

### Radio meshes → A's EUDs

1. CoT arrives on B's wlan1 or halow0.
2. B's smcroute forwards wlan1 → br-lan (existing route) and wlan1 → eth0
   (`mesh_relay`). halow0 is forwarded the same way.
3. A's smcroute forwards eth0 → br-lan to its EUDs.

### LoRa → A's EUDs

1. B's `cot-bridge` decodes LoRa CoT and sends it on br-lan from a local
   socket (`SO_BINDTODEVICE br-lan`, TTL 32, `IP_MULTICAST_LOOP` on).
2. Loopback hands the packet to the kernel's multicast forwarding table.
   smcroute's br-lan → eth0 route forwards it to A (and br-lan → wlan1/halow0
   onto the meshes). Verified on node 0042: a packet sent from such a socket
   created `(10.20.42.1, 239.2.3.1) Iif: br-lan Oifs: eth0 wlan1` and
   incremented eth0 PktsOut in `/proc/net/ip_mr_vif`.

This path already works in `br_lan` mode. `mesh_relay` is not needed for it.

## LoRa source filter

`cot-bridge` sends multicast to LoRa only from accepted sources, so it does not
retransmit Wi-Fi mesh traffic that other nodes already carry. Accepted sources:

- this node's br-lan subnet (always),
- `10.20.<id>.0/24` for each ID in `eth0.mcast_source_node_ids`, in
  `mesh_relay` only,
- **except** `10.20.<id>.1` (the source node's br-lan gateway address). A
  source node's own cot-bridge injects the LoRa CoT it receives on its br-lan
  from that address (unbound socket on br-lan, kernel selects the primary br-lan
  address; seen live on 0042 as `(10.20.42.1,239.2.3.1) Iif: br-lan`). Without
  the exclusion, B would re-transmit on LoRa any CoT that A heard and B missed.
  EUDs get DHCP leases from the pool (offset 10), never `.1`.

The relay subnets are re-read from `config.yaml` every main-loop pass (~10 s).
Changing them takes effect without a `cot-bridge` restart. They are exposed as
`_derived.eth0_mcast_source_subnets` and `_derived.eth0_mcast_source_exclude_ips` in `GET /api/v1/config`.

Limitation: the subnet is derived from the default br-lan scheme
`10.20.<id>.0/24`. If node A overrides `br_lan.subnet_prefix`, its EUDs are
not accepted as LoRa sources (multicast forwarding over Wi-Fi/HaLow is
unaffected).

## Constraints (loops and duplicates)

802.11s RMC deduplicates by (mesh source address, mesh sequence number). The
same IP packet transmitted by two different nodes has two different mesh source
addresses, so RMC does not drop either copy.

| Misconfiguration | Effect |
|------------------|--------|
| **Two `mesh_relay` nodes on one eth0 segment** that share a radio mesh | **Loop.** Relay 1 forwards wlan1 → eth0, relay 2 forwards eth0 → wlan1, relay 1 hears it on wlan1 again, and so on. Each pass costs TTL and airtime until TTL expiry. Never configure this. |
| Node A's wlan1/halow0 on (A in `br_lan`) | **Duplicates, bounded.** A's EUD traffic goes out over the air from both A (br-lan → wlan1) and B (eth0 → wlan1). Mesh traffic reaches A's EUDs twice (via wlan1 and via eth0). No loop, because A has no eth0 ↔ radio route. |
| Node A's Meshtastic RX on (`tx_enabled: false`) | **Handled automatically.** A's cot-bridge injects received LoRa CoT on its br-lan from A's gateway address `10.20.A.1`; it crosses the wire to B. In `mesh_relay`, B excludes `10.20.<id>.1` of every source node from its LoRa sources, so that copy is never re-sent on LoRa. It is still forwarded onto wlan1/halow0; ATAK deduplicates by CoT UID. |

The schema enforces only per-node rules: `mesh_relay` requires `eth0.babel`,
and a node may not list its own ID. It cannot see other nodes' configuration, so
the one-relay-per-segment rule and A's RF-silence are **operator
responsibilities**.

## Firewall

`mesh_relay` adds UFW forward rules `eth0 → wlan1` and `eth0 → halow0` for the
ATAK groups. In eth0 `lan` mode it also adds the reverse direction; in `wan`
mode the reverse is already covered by the internet-egress rules. Inbound ATAK
multicast on eth0 is already allowed in `br_lan`/`mesh_relay`. See
[Firewall](firewall.md).

## Verification (node B)

```bash
# Rendered routes present
grep -E 'mroute from (eth0|wlan1|halow0) group 239.2.3.1 to (wlan1|halow0|eth0)' /etc/smcroute.conf

# Kernel forwarding entries once ATAK traffic flows
ip mroute show | grep 239.2.3.1
#   (10.20.A.x,239.2.3.1)  Iif: eth0   Oifs: br-lan wlan1 halow0
#   (10.20.N.x,239.2.3.1)  Iif: wlan1  Oifs: br-lan eth0

# Per-interface multicast counters (eth0 PktsIn, wlan1 PktsOut rising)
cat /proc/net/ip_mr_vif

# LoRa source filter picked up the relay subnets
journalctl -u cot-bridge | grep 'TX relay source subnets'

# A's CoT going out on LoRa
journalctl -u cot-bridge -f | grep 'TX → LoRa'
```

If `ip mroute` shows `Iif: eth0` with no radio interface in `Oifs`, smcroute is
running an old config: re-run apply, check `systemctl status smcroute`.

## Implementation

| File | Role |
|------|------|
| `nucleusd/schema.py` | `Eth0Config.mcast_mode`, `mcast_source_node_ids`, legacy `mcast_bridge` migration; `NucleusConfig.eth0_mcast_relay_ifaces`, `eth0_mcast_source_subnets`, `eth0_mcast_source_exclude_ips`, `eth0_link_routes`; `_eth0_mcast_relay_gate` validator |
| `nucleusd/templates/smcroute.conf.j2` | eth0 ↔ wlan1/halow0 `mroute`s from `eth0_mcast_relay_ifaces` |
| `nucleusd/templates/nucleus-firewall.sh.j2` | UFW forward rules from `eth0_link_routes` (template unchanged) |
| `nucleusd/meshtastic/cot_bridge.py` | `MCAST_SOURCE_SUBNETS`/`MCAST_SOURCE_EXCLUDE_IPS` in `_read_mesh_conf`, `_apply_mcast_source_subnets`, `_tx_source_ok` |
| `nucleusd/web/app.js` | eth0 section: ATAK mcast mode, Relay source node IDs |
| `tests/test_eth0_link.py`, `tests/test_cot_bridge_relay_source.py` | schema, render, firewall, source filter |
