# Config page (RADIO CONFIGURATION)

Schema-driven form for the whole node config (`/etc/nucleus/config.yaml`).
Operators set primitives; each section header shows the **derived** values the
schema computes from them. Editing mutates a working copy that is saved as one
read-modify-write `PUT /api/v1/config`.

## Node

Header: derived hostname and mesh IP.

| Field | Notes |
|-------|-------|
| Node id (blank=hostname) | 1–254; blank derives from hostname. |
| Name (hostname override) | Optional hostname override. |

## Wifi Mesh

Header: derived mesh subnet.

| Field | Notes |
|-------|-------|
| SSID | Mesh SSID. |
| Channel (2.4GHz) | 1–13. |
| Password (min 8) | Mesh password. |
| Subnet prefix | Optional. |
| Country | Regulatory country (default US). |
| Enabled (`mesh.enabled`) | `on` / `off`. Off rfkill-blocks the mesh radio and stops the mesh units. Also toggled live from the [Radios](radios.md) page; persists across reboots. |
| mcast TTL | 0–64. |
| 802.11s TTL | 0–31. |
| RTS threshold (0=off) | 0–2347. |

## Access Point

Header: derived AP SSID.

| Field | Notes |
|-------|-------|
| Channel (5GHz) | 1–165. |
| Password (min 8) | AP password. |
| SSID override | Optional. |

## br-lan

Header: derived br-lan IP.

| Field | Notes |
|-------|-------|
| Subnet prefix (blank=auto) | Optional. |
| DHCP pool offset | 0–254. |
| DHCP pool size | 1–254. |
| DNS | Upstream DNS (default 8.8.8.8). |

## eth0

| Field | Notes |
|-------|-------|
| Mode | `wan` (DHCP client, shares internet to the mesh via NAT) or `lan` (static gateway serving DHCP to a directly-connected device). For a direct Pi↔Pi (or fiber) link, flip one node to `lan` so it becomes the DHCP server and the other stays `wan` as its client. |
| DHCP pool offset | 0–254 (lan mode). |
| DHCP pool size | 1–254 (lan mode). |
| Babel routing | `off` (default) / `on`. Runs Babel on eth0 (`type wired`, rxcost 96) so nodes joined by a hard line (fiber/copper, direct or via a switch) exchange routes; the wire is preferred over radio. Opens UDP 6696 on eth0. |
| ATAK mcast mode | `eth0.mcast_mode`. `off` (default): no eth0 multicast. `br_lan`: smcroute bridges the ATAK groups eth0 ↔ br-lan (no-echo), and TTL=1 ATAK multicast arriving on eth0 is raised to `mesh.mcast_ttl`. `mesh_relay`: also eth0 ↔ wlan1 and eth0 ↔ halow0 (if HaLow enabled), so an RF-silent node on the wire reaches the radio meshes through this node. Requires Babel routing on. |
| Relay source node IDs | `eth0.mcast_source_node_ids`, `mesh_relay` only. Node IDs (hostname `NNNN-nucleus` → `NNNN`) of the RF-silent nodes on the wire. Their br-lan subnets (`10.20.<id>.0/24`) are accepted by the CoT bridge as multicast → LoRa sources. |

**`mesh_relay` constraints:** at most one `mesh_relay` node per eth0 segment, and
the other nodes on the wire must have no radio TX (`mesh.enabled`, HaLow,
`meshtastic.tx_enabled` off). Otherwise ATAK multicast loops wire ↔ mesh, or goes
out over the air twice, until TTL expiry. Set on the relay node only:
`mcast_mode: mesh_relay`, enter the RF-silent node IDs, then Save and Apply.
Full reference — paths, TTL, LoRa filter, loop analysis, verification:
[eth0 mesh relay](eth0-mesh-relay.md).

### Wired node link (eth0) — turning it on

Off by default: a node does nothing on eth0 beyond normal WAN/LAN until this is
set. Do it on **every** node on the wire (fiber media converter, copper, or a
switch).

1. Web UI → RADIO CONFIGURATION → eth0:
   - **Babel routing** → `on`
   - **ATAK mcast mode** → `br_lan`
2. **Save** → **Dry-run apply** (expect `/etc/babeld.conf`, `/etc/smcroute.conf`,
   `nucleus-firewall.sh` changed) → **Apply**.

Or edit `/etc/nucleus/config.yaml`, then `sudo nucleusctl apply`:

```yaml
eth0:
  babel: true
  mcast_mode: br_lan
```

Write `mcast_mode: "off"` quoted if you turn it back off by hand — unquoted
`off` is a YAML boolean and fails validation.

**Cabling:**
- Direct node↔node cable (no router): set **Mode** → `lan` on ONE node (it
  serves DHCP); the other stays `wan`.
- Via a switch/router that serves DHCP: leave both on `wan`.

**Check it works** (on either node):
- `echo dump | nc ::1 33123 | grep 'neighbour.*if eth0'` — the peer node is listed.
- `ip route | grep 'dev eth0 proto babel'` — the peer's `10.20.<id>.0/24` is via eth0.
- `smcroutectl show routes | grep eth0` — eth0 ↔ br-lan ATAK routes are present.
- ATAK on an EUD on one node sees EUDs on the other.

For an RF-silent node on the wire that reaches the radio meshes through a relay
node, do the above on both nodes first, then [eth0 mesh relay](eth0-mesh-relay.md).

Both work in `wan` and `lan` mode. babeld/smcroute run whenever any of the wlan1
mesh, HaLow, or these eth0 options needs them — `mesh.enabled: false` no longer
stops them. eth0 → internal forwarding is limited to Nucleus nets (10.20.0.0/16,
10.30.1.0/24) and the ATAK groups, and WAN NAT exempts those nets + multicast so
hard-line traffic keeps its real source. On a shared/home LAN, the bridged ATAK
multicast is visible to that whole LAN.

In `lan` mode the rendered network config also enables a router-less IPv6
link-local (`LinkLocalAddressing=ipv6`, `IPv6AcceptRA=no`) so Reticulum's
AutoInterface can peer over eth0 with no router/RA present. The `wan` branch is
unchanged (it owns the DHCP/DNS path).

eth0 is owned by systemd-networkd only. On apply, once the eth0 network config
exists and systemd-resolved is active, NetworkManager is taken off eth0 (marked
unmanaged, its netplan eth0 file removed) so only one DHCP client runs there.
DNS learned on eth0 reaches `/etc/resolv.conf` through systemd-resolved.

## Meshtastic Radio

| Field | Notes |
|-------|-------|
| Enabled | `on` / `off`. Runs/stops `meshtasticd` entirely (for nodes without the HAT). |
| TX enabled (`tx_enabled`) | `on` / `off`. Off = **receive-only**: the radio still receives but never transmits. Toggled live from the [Radios](radios.md) page; persists across reboots. |
| Region | Regulatory region. |
| HAT / slot | `rak6421-slot1` / `rak6421-slot2` / `auto`. |
| GPS | `off` / `uart` / `i2c`. |
| GPS serial path | Default `/dev/ttyS0`. |
| I2C device | Default `/dev/i2c-1`. |
| CoT bridge | `on` / `off`. |
| TX rate limit (s) | 0–3600; min secs between LoRa TX of the same CoT UID (`0` = off). Read live by the bridge. See [Meshtastic](meshtastic.md#tx-rate-limit). |

## Web UI

Controls access to this web interface. Clients on the mesh, the access point,
the local wired LAN (br-lan), Tailscale, or the node itself reach the UI with
**no password**. A client connecting over the **ethernet port** (e.g. the node
plugged into a home or office network) must log in.

| Field | Notes |
|-------|-------|
| Web password (min 6) | HTTP Basic password for access over ethernet. Username is `admin`. |
| Allow web UI over ethernet | `on` / `off`. Off closes ports 80/443 on eth0 entirely. See [Firewall](firewall.md). |

**Default login: `admin` / `52235223` — change it.** The password is sent over
plain HTTP; on an untrusted network use Tailscale to reach the UI instead. The
raw app port (8080) is never exposed on ethernet — only the nginx proxy.

## Applying changes

- **Save (not applied)** — validates and writes config; nothing on the system
  changes yet.
- **Dry-run apply** — reports which files *would* change and which units would
  restart, without doing it.
- **Apply** — writes the changed files and restarts affected units. Reports
  `no changes — system in sync` when already applied.
- **Reload** — discards the working copy and re-reads the saved config.
