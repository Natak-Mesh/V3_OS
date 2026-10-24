# Host firewall (UFW)

Every node runs a host firewall (UFW). It is not a web UI page — it is driven
entirely from `config.yaml`. This page documents what the ruleset is, how it is
applied, and the `firewall.enabled` switch.

## Principle: config is the single source of truth

The firewall rules are rendered into `nucleus-firewall.sh` and applied by
`nucleus-firewall.service` — a `oneshot` unit ordered only `After=` networkd,
with **no dependency on the wlan1 mesh radio**. It runs at boot and whenever
`nucleusctl apply` changes the script. The script runs `ufw --force reset`
**first**, then re-adds exactly the rules the current config asks for. Because of
the reset, a rule that is no longer in the config (for example, the web-over-eth0
rules after you set `eth0_access: off`) is removed rather than left behind. The
live ruleset therefore always reflects the current `config.yaml`, with one
exception: the optional TAK Server / MediaMTX add-on (provisioned by
`nucleus-tak-setup.sh`, outside the config pipeline) opens its eth0 ports based
on what is installed on the node, checked at run time. See the table below.

The firewall is **decoupled from the mesh on purpose.** It used to live in
`nucleus-mesh-up.sh` (run by `nucleus-mesh.service`, which depends on wlan1). If
wlan1 was RF-killed, that script aborted at the mesh-join step under
`set -euo pipefail` *before* the UFW block, so eth0 firewall rules — including
the Reticulum UDP ports needed for wired peering — never applied. Splitting UFW
into its own radio-independent unit fixes that: the host firewall comes up even
with the mesh radio down.

The reset only clears UFW's own rules. It does **not** touch:

- the nftables NAT/masquerade rule — eth0 WAN internet sharing, set up at the top
  of `nucleus-firewall.sh` (same script, separate nftables table),
- the multicast-TTL mangle rules (`nucleus_mcast_ttl`), set up by
  `nucleus-mesh-up.sh`,
- Tailscale's own firewall chains.

Those live in separate nftables tables (or in Tailscale) and are unaffected.

## The ruleset

With the firewall enabled, the following rules are applied, in this order:

| Rule | Purpose |
|------|---------|
| default deny incoming | Nothing gets in unless a rule below allows it. |
| default allow outgoing | The node can reach out freely. |
| default deny routed | No forwarding unless a route rule below allows it. |
| allow in on eth0 port 22/tcp | **ssh over ethernet — always allowed.** Added first, right after the reset, so ethernet admin access is never left dangling by a later bad rule. |
| allow in on eth0 port 29716/29717/42671 udp | Reticulum AutoInterface over eth0 (wired/fiber peering): discovery, unicast discovery and data. Required or Reticulum peers never form over the wire. Always present. |
| allow in on wlan1 | Trust the 802.11s mesh (WPA3-encrypted). |
| allow in on br-lan | Trust the local wired LAN / AP bridge. |
| allow in on tailscale0 | Trust Tailscale. |
| allow in on eth0 port 80/443 tcp | Web UI over ethernet. Only present when `web.eth0_access: true`. nginx password-protects these. |
| allow in on eth0 port 8443/8089/8446 tcp, 8090 udp | Official TAK Server over ethernet. Only present when the `takserver` package is installed — checked at run time with `dpkg-query`, not from config. Open to any source; see [TAK Server](takserver.md#ports). |
| allow in on eth0 port 8444/8443/8446/8089/8883/8322/1936 tcp | OpenTAKServer over ethernet. Only present when `opentakserver.service` is enabled — checked at run time with `systemctl is-enabled`, not from config. 8082 (plain HTTP, unauthenticated) is never opened on eth0. Open to any source; see [OpenTAKServer](opentakserver.md#ports). |
| allow in on eth0 port 8554 tcp, 8000/8001/8890 udp | MediaMTX RTSP + SRT video over ethernet. Only present when `mediamtx.service` is enabled — checked at run time with `systemctl is-enabled`, not from config. Open to any source; see [TAK Server](takserver.md#mediamtx). |
| route allow br-lan/wlan1 → eth0 | Internet egress for clients. Only in eth0 WAN mode. |
| route allow br-lan ↔ wlan1 | Mesh ↔ LAN forwarding, always. |

The raw app port (8080) is never opened on eth0 — only the nginx proxy (80/443)
is exposed there, and only when `eth0_access` is on. See [Config](config.md) for
the web access toggle and [System](system.md) for eth0 WAN/LAN mode.

## Turning the firewall off

```yaml
firewall:
  enabled: true    # false = disable UFW entirely (no host firewall)
```

With `enabled: false`, the script runs `ufw --force disable` and renders no
rules at all. Only do this if another firewall protects the node — with UFW off,
eth0 is wide open apart from whatever the nftables/Tailscale rules cover.

## Applying and verifying

The firewall is re-applied whenever `nucleus-firewall.sh` changes, i.e. after
`sudo nucleusctl apply` (which restarts `nucleus-firewall.service`). It applies
independently of the mesh, so it also comes up on a node with wlan1 RF-killed.
To inspect the live state:

```bash
sudo ufw status numbered   # rules currently loaded
sudo ufw status            # active / inactive
# verify the eth0 Reticulum ports are open even with the mesh radio down:
sudo ufw status | grep -E '2971[67]|42671'
```

There is a brief moment during `reset` + rebuild where the firewall is open.
This only happens while `nucleus-firewall.service` runs (boot or apply). A bad
rule after the reset could cut off ssh on eth0 or partition the mesh, so keep a
second way in (mesh or Tailscale) when changing firewall config on a remote node.
