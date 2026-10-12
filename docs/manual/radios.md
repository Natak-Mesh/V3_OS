# Radios page (RADIOS — RF ON/OFF)

Take an individual radio on this node on or off **without shutting the node
down**, so a wired link keeps working. Each radio is its own switch — there is
**no "kill all" master switch**. The page shows one line per radio with an
inline `ON` / `OFF` / `—` (unknown) switch, refreshed every 5 s from
`GET /api/v1/status` (`.radios`).

## The three switches

| Radio | What "off" does | Persistence |
|-------|-----------------|-------------|
| **Wi-Fi mesh** (wlan1) | rfkill-blocks phy1 and stops `nucleus-mesh` / `babeld` / `smcroute`. The node leaves the 802.11s mesh. | **Persists across reboots** — stored as `mesh.enabled` in `config.yaml`. |
| **Meshtastic** (LoRa) | **Receive-only**: the radio stays powered and keeps receiving, but never transmits. CoT, text, voice, stream and heartbeat LoRa TX are all gated off. | **Persists across reboots** — stored as `meshtastic.tx_enabled`. |
| **Access point** (wlan0) | rfkill-blocks phy0 and stops `hostapd`. | **Live only** — **always resets to ON at the next reboot** (a lockout-recovery path). |

The RNode LoRa radio is controlled separately on the [Reticulum](reticulum.md)
page (`reticulum.rnode_enabled`), not here.

## How the switches apply

- **Mesh and Meshtastic are config-backed.** Toggling one does a
  read-modify-write `PUT /api/v1/config` (setting `mesh.enabled` or
  `meshtastic.tx_enabled`) then `POST /api/v1/apply` — the same stage-then-apply
  flow as the [Config](config.md) page. That is why the state survives a reboot.
- **The access point is a live toggle**, outside the config pipeline (README
  §4), driven by `POST /api/v1/rf/ap`. It is never written to `config.yaml`, so
  it always comes back ON at boot.

### Turning the mesh back on

Turning the mesh **on** is not just an rfkill unblock. Blocking the radio
bounces wlan1, which drops its IPv6 link-local and disconnects the SAE join, and
Babel ignores an interface with no link-local. So on the off→on transition the
apply **restarts** `nucleus-mesh`, `babeld` and `smcroute`, which redoes the
WPA3/SAE join and restores the link-local. No manual step is needed.

## Lockout warning

Turning off the **mesh** or the **access point** can cut the link you are using
to reach this node — the UI asks you to confirm first. Wired fallbacks that are
unaffected: **USB-C (RNDIS), eth0, or the console**. The access point also
always returns at the next reboot as a recovery path.

## Verify after toggling the mesh on

On the node (the header's `mesh` indicator should return to `N connected`):

```bash
ip -6 addr show wlan1 | grep fe80          # link-local present
echo dump | nc ::1 33123 | grep 'wlan1'    # expect: add interface wlan1 up true
nucleusctl status | jq .radios             # per-radio state
```

## Config keys

```yaml
mesh:
  enabled: true          # false = mesh radio off (persists)
meshtastic:
  tx_enabled: true       # false = Meshtastic receive-only (persists)
```

Both default to `true` and are the single source of truth in `schema.py`; the
page, `nucleusctl` and the apply reconcile all consume them. See
[API.md](../API.md) for `POST /api/v1/rf/ap` and the `radios` block of
`GET /api/v1/status`.
