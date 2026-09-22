# Host firewall (UFW)

Every node runs a host firewall (UFW). It is not a web UI page — it is driven
entirely from `config.yaml` and rebuilt on each mesh bring-up. This page
documents what the ruleset is, how it is applied, and the `firewall.enabled`
switch.

## Principle: config is the single source of truth

The firewall rules are rendered into `nucleus-mesh-up.sh` and applied by
`nucleus-mesh.service` every time the mesh comes up (boot, or after
`nucleusctl apply` changes the script). The script runs `ufw --force reset`
**first**, then re-adds exactly the rules the current config asks for. Because of
the reset, a rule that is no longer in the config (for example, the web-over-eth0
rules after you set `eth0_access: off`) is removed rather than left behind. The
live ruleset therefore always reflects the current `config.yaml` and nothing
else.

The reset only clears UFW's own rules. It does **not** touch:

- the nftables NAT/masquerade rule (WAN mode internet sharing),
- the multicast-TTL mangle rules (`nucleus_mcast_ttl`),
- Tailscale's own firewall chains.

Those live in separate nftables tables set up elsewhere in the same script (or by
Tailscale) and are unaffected.

## The ruleset

With the firewall enabled, the following rules are applied, in this order:

| Rule | Purpose |
|------|---------|
| default deny incoming | Nothing gets in unless a rule below allows it. |
| default allow outgoing | The node can reach out freely. |
| default deny routed | No forwarding unless a route rule below allows it. |
| allow in on eth0 port 22/tcp | **ssh over ethernet — always allowed.** Added first, right after the reset, so ethernet admin access is never left dangling by a later bad rule. |
| allow in on wlan1 | Trust the 802.11s mesh (WPA3-encrypted). |
| allow in on br-lan | Trust the local wired LAN / AP bridge. |
| allow in on tailscale0 | Trust Tailscale. |
| allow in on eth0 port 80/443 tcp | Web UI over ethernet. Only present when `web.eth0_access: true`. nginx password-protects these. |
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

The firewall is re-applied whenever `nucleus-mesh-up.sh` changes and the mesh is
brought back up, i.e. after `sudo nucleusctl apply`. To inspect the live state:

```bash
sudo ufw status numbered   # rules currently loaded
sudo ufw status            # active / inactive
```

There is a brief moment during `reset` + rebuild where the firewall is open.
This only happens at mesh-up, while services are starting anyway. A bad rule
after the reset could cut off ssh on eth0 or partition the mesh, so keep a second
way in (mesh or Tailscale) when changing firewall config on a remote node.
