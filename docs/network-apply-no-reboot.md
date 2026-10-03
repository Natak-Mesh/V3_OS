# Network apply without reboot

Status: PLANNED — not implemented. Do this work on a feature branch (e.g.
`feature/network-apply`), NOT `main`. Bench-test on a spare node before merging.

## Problem

`nucleusctl apply` is meant to make config changes live without a reboot. In
practice the network is often left half-broken until the node is rebooted:

- AP (wlan0) is visible but clients cannot join. hostapd logs nothing, and
  br-lan reports `Offered DHCP leases: none`.
- The mesh (wlan1) shows a peer in the web UI and unicast works (ping, Babel
  routes), but ATAK multicast (239.2.3.1:6969) from local EUDs never crosses the
  mesh.
- A reboot fixes it.

Seen on node 50 (2026-10-02/03): the 18:47 boot skipped hostapd (empty
hostapd.conf), nucleus-mesh failed at 18:51, and an apply at 19:03 brought the
units "up". The AP stayed unusable until a reboot.

## Root causes

1. **Dependent daemons are not restarted.** `apply()` restarts only the units
   whose own rendered files changed (`TARGETS` in `nucleusd/apply.py`). When
   wlan1, br-lan or hostapd are bounced, smcroute, babeld, cot-bridge,
   nucleus-messaging, nucleus-voice and rnsd keep running against stale
   interfaces and multicast memberships. No unit has `PartOf=`/`BindsTo=`, so
   systemd does not propagate restarts either.

2. **`nucleus-mesh-up.sh` is not safe to re-run.** It runs
   `wpa_supplicant -B -i wlan1 ...` without stopping the instance already
   running. On a re-run that fails and, under `set -euo pipefail`, the script
   aborts before the UFW and multicast-TTL mangle sections.

3. **Adding wlan0 to br-lan is a race.** Three things touch it: networkd
   (`Bridge=br-lan` in `30-wlan0.network`), hostapd putting wlan0 in AP mode,
   and `brlan-setup.service` (a fixed `sleep 2`, then `ip link set wlan0 master
   br-lan`). Restart them in a different order and wlan0 ends up outside the
   bridge, so AP clients get no DHCP.

4. **`networkctl reload` does not reconfigure existing links.** `_restart()`
   uses `reload` for systemd-networkd, which only re-reads files. Links that
   already exist need `networkctl reconfigure <link>`.

5. **No hostapd control socket.** `hostapd.conf` has no `ctrl_interface=`, so
   `hostapd_cli` cannot connect, which makes AP problems hard to diagnose.

## Fixes

### A. Make dependent daemons restart with the mesh
Add `PartOf=nucleus-mesh.service` (plus a matching `After=`) to:
- `system/systemd/cot-bridge.service`
- `system/systemd/nucleus-messaging.service`
- `system/systemd/nucleus-voice.service`
- `system/systemd/rnsd.service`
- smcroute, babeld and hostapd. These are distro units, so use drop-ins
  following the existing `system/systemd/takserver.service.d/override.conf`
  pattern:
  - `system/systemd/smcroute.service.d/override.conf`
  - `system/systemd/babeld.service.d/override.conf`
  - `system/systemd/hostapd.service.d/override.conf`

`install.sh` must install the new drop-ins and run `systemctl daemon-reload`.
Check how the takserver drop-in is installed and copy that approach.

Restart order to avoid: cot-bridge `Requires=meshtasticd`. Check that `PartOf`
does not cause meshtasticd restarts or loops.

### B. Make `nucleus-mesh-up.sh` re-runnable
In `nucleusd/templates/nucleus-mesh-up.sh.j2`, before starting wpa_supplicant:
- Kill any existing wpa_supplicant bound to wlan1, e.g.
  `pkill -f 'wpa_supplicant .*-i wlan1' || true`, then wait for it to exit.
- Remove a stale control socket, e.g. `/var/run/wpa_supplicant/wlan1`.
- Optional: replace `sleep 15` with polling `iw dev wlan1 station dump` for an
  ESTAB peer, with a timeout.

### C. Have `apply` rebuild the network stack in a fixed order
In `nucleusd/apply.py`:
- Add a network-group flag. If ANY networkd, wpa_supplicant-mesh,
  nucleus-mesh-up, hostapd, babeld or smcroute target changed, restart the
  whole group, not just the changed units.
- Order: `networkctl reconfigure` (br-lan, wlan1, eth0) → nucleus-mesh →
  hostapd → brlan-setup → babeld → smcroute → cot-bridge / messaging / voice /
  rnsd. (With fix A most of the tail restarts on its own, but be explicit.)
- `_restart()`: use `networkctl reconfigure <links>` instead of
  `networkctl reload`, or run reload then reconfigure.
- Do NOT touch the network group if no network file changed. Unrelated changes
  (voice, messaging, nginx) must stay non-disruptive.

### D. Remove the wlan0/br-lan race
- `system/systemd/brlan-setup.service`: replace `sleep 2` with a bounded wait
  until `iw dev wlan0 info` reports `type AP`, then enslave and run
  `networkctl reconfigure br-lan`. Fail loudly on timeout.
- Pick ONE owner for adding wlan0 to the bridge: either networkd's
  `Bridge=br-lan` in `nucleusd/templates/networkd/30-wlan0.network.j2` or
  brlan-setup, not both. Note the existing warning in hostapd.conf about
  double-enslaving.

### E. hostapd diagnostics
In `nucleusd/templates/hostapd.conf.j2` add:
- `ctrl_interface=/var/run/hostapd`
- `country_code=<mesh.country>` (and optionally `ieee80211d=1`) so 5 GHz
  channels are explicitly allowed.

## Files touched (summary)
- `nucleusd/apply.py`
- `nucleusd/templates/nucleus-mesh-up.sh.j2`
- `nucleusd/templates/hostapd.conf.j2`
- `nucleusd/templates/networkd/30-wlan0.network.j2` (if networkd stops owning
  the bridge port)
- `system/systemd/brlan-setup.service`
- `system/systemd/{cot-bridge,nucleus-messaging,nucleus-voice,rnsd}.service`
- `system/systemd/{smcroute,babeld,hostapd}.service.d/override.conf` (new)
- `install.sh` (install drop-ins)
- `tests/test_render.py` (rendered hostapd.conf / mesh-up script assertions)
- New test for the apply restart grouping and ordering (mock `subprocess.run`)

## Test checklist (bench node, two-node mesh)
For EACH of these changes, run `sudo nucleusctl apply` with no reboot and
confirm everything below:

Changes:
- [ ] mesh channel / password
- [ ] AP channel / password
- [ ] br_lan DHCP pool
- [ ] mcast_ttl / smcroute groups
- [ ] an unrelated change (voice/messaging): network must NOT bounce
- [ ] running apply twice in a row (idempotent, second run is "no changes")

Checks:
- [ ] Phone joins the AP and gets a 10.20.<id>.x lease
      (`networkctl status br-lan`)
- [ ] `hostapd_cli -i wlan0 all_sta` lists the phone
- [ ] wlan0 is a br-lan port (`bridge link`)
- [ ] Mesh peer is ESTAB (`iw dev wlan1 station dump`) and the Babel route to the
      peer's /24 exists
- [ ] `sudo ip -s mroute` shows the local EUD's (S,239.2.3.1) br-lan→wlan1 with
      the packet counters increasing
- [ ] ATAK EUDs on both nodes see each other
- [ ] UFW rules and the `nucleus_mcast_ttl` mangle rules are present
      (`sudo nft list table ip mangle`)
- [ ] Only one wpa_supplicant runs on wlan1 (`pgrep -a wpa_supplicant`)
- [ ] cot-bridge, messaging, voice and rnsd are active and logging normally
- [ ] LoRa heartbeats are still received on the far node

## Out of scope / separate
- LoRa heartbeat not received on node 49 (node 50 does receive node 49's LoRa
  traffic). Investigate separately.
- `update.sh` keeps "reboot recommended" until this is merged and proven.
