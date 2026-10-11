# Configuration (`config.yaml`)

The whole node config lives in `/etc/nucleus/config.yaml`. Operators set
primitives and the schema derives the rest from `node.id`. Apply with
`sudo nucleusctl apply`.

## `node`

Derived: hostname (`<id:04d>-nucleus`) and mesh IP.

| Key | Notes |
|-------|-------|
| `id` | 1–254. Leave unset to derive it from the hostname. |
| `name` | Optional hostname override. |

## `mesh`

Derived: mesh subnet.

| Key | Notes |
|-------|-------|
| `ssid` | Mesh SSID. |
| `channel` | 2.4 GHz, 1–13. |
| `password` | Mesh password (min 8). |
| `subnet_prefix` | Optional. |
| `country` | Regulatory country (default US). |
| `mcast_ttl` | 0–64. |
| `mesh_802_ttl` | 0–31. |
| `rts_threshold` | 0 = off. |

## `ap`

Derived: AP SSID (`<id:04d>-nucleus-ap`).

| Key | Notes |
|-------|-------|
| `channel` | 5 GHz. |
| `password` | AP password (min 8). |
| `name` | Optional SSID override. |

## `br_lan`

Derived: br-lan IP (`10.20.<id>.1`).

| Key | Notes |
|-------|-------|
| `subnet_prefix` | Optional, default `10.20.<id>`. |
| `dhcp_pool_offset` | 0–254. |
| `dhcp_pool_size` | 1–254. |
| `dns` | Upstream DNS (default 8.8.8.8). |

## `eth0`

| Key | Notes |
|-------|-------|
| `mode` | `wan` or `lan`. |
| `dhcp_pool_offset` | 0–254 (lan mode). |
| `dhcp_pool_size` | 1–254 (lan mode). |

## `firewall`

| Key | Notes |
|-------|-------|
| `enabled` | `false` disables UFW entirely. See [Firewall](firewall.md). |

## Applying changes

- `sudo nucleusctl validate` checks the file. Nothing on the system changes.
- `sudo nucleusctl apply --dry-run` reports which files *would* change and which
  units would restart, without doing it.
- `sudo nucleusctl apply` writes the changed files and restarts affected units.
