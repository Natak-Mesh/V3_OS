# HaLow Mesh page (HALOW MESH)

Status and configuration for the optional Wi-Fi HaLow (802.11ah) sub-GHz mesh,
carried on a Morse Micro MM8108 USB radio presented as `halow0`. This is a
**second 802.11s mesh** alongside the wlan1 backbone, on its own subnet
(10.30.1.\<id\>/24), long-range and low-bandwidth.

The `HALOW MESH` menu entry — and this page — appear **only when the radio is
plugged in** (the `halow0` interface is present). Pull the radio and the entry
disappears. Engineering notes and the regulatory channel tables are in
[docs/halow.md](../halow.md); recognised USB hardware in
[usb-devices.md](usb-devices.md).

## Status

Read-only, refreshed every 5s from `GET /api/v1/status` (`.radios.halow`) and
`GET /api/v1/config` (`._derived`):

| Field | Meaning |
|-------|---------|
| Interface | `present (halow0)` when the radio is plugged in, else `absent`. |
| IP | This node's HaLow mesh IPv4 (10.30.1.\<id\>). |
| Channel | The real S1G channel number and its centre frequency in MHz. **Not** the 5 GHz stand-in channel the driver shows to `iw` (see [docs/halow.md §6](../halow.md)). |
| Bandwidth | Channel bandwidth in MHz (1 / 2 / 4 / 8). |
| Service | `nucleus-halow` systemd unit state (`active` green, else grey). |

The neighbours this node reaches over HaLow are listed on the
[Monitor](monitor.md) page under **HaLow mesh nodes**.

## Settings

Config-backed and edited the same way as the [Config](config.md) page — edits are
staged, then saved and applied:

| Field | Meaning |
|-------|---------|
| Enabled | Run the HaLow mesh (`halow.enabled`). Off leaves the radio inert even while plugged in. |
| Bandwidth (MHz) | 1 / 2 / 4 / 8. The valid channel set depends on it. |
| Channel (S1G) | US S1G channel number. The dropdown only offers channels valid for the selected bandwidth — the list comes from the schema (`_derived.halow_channels`), never a copy in the UI. Changing bandwidth snaps the channel to a valid one. |
| Primary 1MHz index | Which 1 MHz sub-channel is primary, `0 .. bandwidth-1`. |

The **SSID, password and country are shared with the Wi-Fi mesh** and are shown
read-only here: the HaLow SSID is the wlan1 mesh SSID with a `_halow` suffix, the
password is `mesh.password`, and the country is `mesh.country`. Change them on the
[Config](config.md) page. Only the US S1G channel table is defined, so HaLow
requires `mesh.country == US` — the schema rejects anything else.

## How changes apply

Save does a read-modify-write `PUT /api/v1/config` (overlaying the `halow:`
block), which `schema.py` validates — an out-of-range channel, bandwidth or
primary index is rejected there, and the UI shows the error. Apply
(`POST /api/v1/apply`) renders the HaLow templates and reconciles the
`nucleus-halow` service: enabled/disabled to match `halow.enabled`, and
restarted when the supplicant config or bring-up script changed so a live
channel/bandwidth edit rejoins the mesh.

Every node on the HaLow mesh must share the **same channel, bandwidth and
primary index** to link — change them on all nodes together.

## Verify after an apply

On the node:

```bash
systemctl is-active nucleus-halow        # active
ip -br addr show halow0                   # UP with 10.30.1.<id>/24
iw dev halow0 station dump | grep plink   # mesh plink: ESTAB per peer
```

Then the [Monitor](monitor.md) page's **HaLow mesh nodes** table should list the
peers.
