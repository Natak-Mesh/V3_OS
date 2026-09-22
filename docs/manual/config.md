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
| Mode | `wan` or `lan`. |
| DHCP pool offset | 0–254 (lan mode). |
| DHCP pool size | 1–254 (lan mode). |

## Meshtastic Radio

| Field | Notes |
|-------|-------|
| Enabled | `on` / `off`. |
| Region | Regulatory region. |
| HAT / slot | `rak6421-slot1` / `rak6421-slot2` / `auto`. |
| GPS | `off` / `uart` / `i2c`. |
| GPS serial path | Default `/dev/ttyS0`. |
| I2C device | Default `/dev/i2c-1`. |
| CoT bridge | `on` / `off`. |

## Web UI

Controls access to this web interface. Clients on the mesh, the access point,
the local wired LAN (br-lan), Tailscale, or the node itself reach the UI with
**no password**. A client connecting over the **ethernet port** (e.g. the node
plugged into a home or office network) must log in.

| Field | Notes |
|-------|-------|
| Web password (min 6) | HTTP Basic password for access over ethernet. Username is `admin`. |
| Allow web UI over ethernet | `on` / `off`. Off closes ports 80/443 on eth0 entirely. |

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
