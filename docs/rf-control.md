# RF control (per-radio on/off)

Status: **IMPLEMENTED** and merged to `main`. This file is the design record;
operator docs live in [manual/radios.md](manual/radios.md).

**Progress**
- ✅ Phase 0 — network-apply re-run fixes B/C/D (see below). Done, tested,
  deployed to node 0042, verified live.
- ✅ Phases 1–6 — the radio feature itself (schema flags, `rf.py`,
  `nucleus-rf.service`, apply reconciles, `/api/v1/rf/ap`, the web UI "Radios"
  page, docs). Done.

**Post-merge fix (mesh "on" was a no-op).** The first cut of `_reconcile_rf()`
only rfkill-unblocked phy1 and ran `systemctl enable --now` on the mesh units —
a no-op when they were already running. But an rfkill bounce drops wlan1's IPv6
link-local and disconnects the SAE join, and Babel ignores a link-local-less
interface, so the radio came back but the node stayed off the mesh until a
manual restart. Fixed by: (1) `_reconcile_rf()` detecting the blocked→unblocked
transition and **restarting** `nucleus-mesh`/`babeld`/`smcroute` in
network-group order (redoing the join); (2) `nucleus-mesh-up.sh` setting
`net.ipv6.conf.wlan1.keep_addr_on_down=1` and running `networkctl reconfigure
wlan1` after the join so the link-local survives the bounce. Regression tests in
`test_apply_rf.py` (transition restarts the mesh) and `test_render.py`
(bring-up script keeps the link-local).

## Problem / goal

Take individual radios on a node on/off without shutting the node down, so a
wired link (USB-C RNDIS, eth0) keeps working. Each radio is its own switch —
there is **no "kill all" master switch**. The switches differ in persistence:

- **wlan1 Wi-Fi mesh** — persists across reboots (lives in `config.yaml`).
- **Meshtastic** — persists across reboots. "Off" means **receive-only, no
  transmit** (the operator is fine with RX continuing; only TX must stop).
- **wlan0 Wi-Fi AP** — live toggle only; **always resets to ON at boot**.

RNode keeps its existing `reticulum.rnode_enabled`; not in scope here.

## Current state (live node, 0042-nucleus)

| Radio | Interface / phy | Controlled today by |
|-------|-----------------|---------------------|
| Wi-Fi mesh | wlan1 / phy1 (USB Alfa) | nothing — `nucleus-mesh`, `babeld`, `smcroute` always run |
| Wi-Fi AP | wlan0 / phy0 (onboard) | nothing — `hostapd` always runs |
| Meshtastic LoRa | SX1262 (RAK13302) | `meshtastic.enabled` → runs/stops `meshtasticd`. Firmware has `lora.tx_enabled` (currently `True`) |
| Reticulum RNode | RAK4631 USB | `reticulum.rnode_enabled` |
| Bluetooth | hci0 | already soft-blocked by `rfkill` |

## Design

Follows the README §1 pipeline: one contract in `schema.py`, hardware access
isolated, idempotent apply. The AP toggle is intentionally **outside** the
pipeline (README §4), like Tailscale.

### 1. wlan1 mesh — persistent, config-driven

- **Schema:** add `mesh.enabled: bool = True` to `MeshConfig`. Source of truth.
- **`nucleusd/rf.py`** (new, the only module that touches rfkill): resolve a phy
  from its interface via `/sys/class/net/<if>/phy80211` (symlink → `phyN`), not
  a fixed `rfkill` index (indices/USB paths aren't stable). `block`/`unblock`
  wrap `rfkill block|unblock <phy>`.
- **Apply** `_reconcile_rf()`: `rfkill` phy1 to match `mesh.enabled`, and use the
  existing `_set_service()` to enable/disable `nucleus-mesh`, `babeld`,
  `smcroute`. Idempotent — only acts on a delta.
- **Boot:** `nucleus-rf.service` (oneshot) runs before `nucleus-mesh` and
  enforces `mesh.enabled` from config, so the state survives reboot.


### 2. Meshtastic — persistent, receive-only when off

"Off" = radio still powered and receiving, but **never transmits**. Use the
firmware setting `lora.tx_enabled`, not stopping `meshtasticd`.

- **Schema:** add `meshtastic.tx_enabled: bool = True`. `meshtastic.enabled`
  keeps its current meaning (run/stop `meshtasticd` entirely, for nodes without
  the HAT).
- **Boot (`radio_init.py`):** each boot, read `lora.tx_enabled` from the radio
  and `--set` it only if it differs from config. Best-effort, never blocks boot
  (matches the existing owner/region pattern).
- **Apply:** `_reconcile_meshtastic_tx()` pushes `lora.tx_enabled` when it
  differs from config. Check on the bench whether a `lora.*` change reboots
  `meshtasticd` (region change does) and handle that like the existing restart.
- **Bridges:** `cot-bridge`, messaging and voice skip LoRa TX and heartbeats
  when `tx_enabled` is false, so they don't queue packets the firmware drops.
  They read config via `config.load()` (README §1).

### 3. wlan0 AP — live toggle, resets ON at boot

Not stored in `config.yaml`; state lives only in the running system (README §4,
"outside the pipeline", like Tailscale).

- **API:** `POST /api/v1/rf/ap` (on/off) → `rfkill` phy0 + `systemctl stop|start
  hostapd` (**stop/start, not disable**, so boot always starts it) + re-run
  `brlan-setup` when it comes back.
- **Boot:** `nucleus-rf.service` runs `After=systemd-rfkill.service` and always
  **unblocks phy0** — because `systemd-rfkill` restores saved block state, a
  previously-blocked AP would otherwise stay blocked after reboot.

### 4. Status

`status.py` reports per-radio state: `mesh.enabled` + live phy1 rfkill;
Meshtastic `tx_enabled`; live phy0 rfkill / hostapd state.

### 5. Web UI — a "Radios" page

A single page in `nucleusd/web/app.js` (page-builder entry keyed `radios`, like
the existing `tailscale`/`reticulum` pages) with one row per radio. It mixes the
two control styles the UI already uses, matching each radio's persistence model:

- **wlan1 mesh** and **Meshtastic TX** are **config-backed** (persist). Their
  toggles `PUT /api/v1/config` (set `mesh.enabled` / `meshtastic.tx_enabled`)
  then `POST /api/v1/apply`, the same stage-then-apply flow the config pages
  already use. State shown comes from `/api/v1/status`.
- **wlan0 AP** is a **live toggle** (resets at boot). Its button hits
  `POST /api/v1/rf/ap` directly, like the Tailscale page drives the CLI outside
  the pipeline. State comes from `/api/v1/status` (phy0 rfkill / hostapd).

Page behaviour:

- `dynamic` refresh (poll `/api/v1/status`) so each row shows live on/off with
  the usual `ok`/`warn`/`off` colouring.
- Meshtastic row label makes clear "off = receive-only (no TX)", not powered
  down, so the operator isn't surprised it still shows RX traffic.
- **Lockout guard:** turning off AP or mesh prompts a confirm that names the
  wired fallbacks (USB-C RNDIS, eth0, console); the AP's reset-on-boot is called
  out as the recovery path. The guard must detect when the request arrives over
  the link being cut and warn harder.
- No new web auth/CSRF handling — reuses the existing `/api/v1` guards.

## Prerequisite: network-apply re-run fixes (B/C/D) — ✅ DONE

Turning a radio **off** is clean (rfkill + stop units). Turning wlan1 **back on**
live re-triggered the known bugs in `network-apply-no-reboot.md`, so without these
a re-enable needed a reboot to cleanly rejoin. These three fixes are the
prerequisite and are now implemented on branch `rfkill`. A/E from that doc are
reliability/diagnostic extras, deliberately out of scope here.

As built:

- **B — `nucleus-mesh-up.sh` is now re-runnable**
  (`nucleusd/templates/nucleus-mesh-up.sh.j2`). Before the wpa_supplicant join it
  runs `pkill -f 'wpa_supplicant .*-i wlan1' || true`, waits (≤5 s) for the
  process to exit, and removes the stale control socket
  `/var/run/wpa_supplicant/wlan1`. Stops the script aborting mid-run under
  `set -euo pipefail` (which otherwise skipped the UFW + mcast-TTL sections).
- **C — apply rebuilds the whole network group in a fixed order**
  (`nucleusd/apply.py`). New `NET_GROUP_ORDER = [systemd-networkd, nucleus-mesh,
  hostapd, brlan-setup, babeld, smcroute]`; if any member's rendered file
  changed, apply bounces the entire group in that order instead of only the
  changed unit (which left the others on stale interfaces/multicast). `_restart()`
  now runs `networkctl reconfigure br-lan wlan1 eth0 wlan0` (new `NETWORKD_LINKS`)
  instead of `networkctl reload`. If no network file changed, the group is left
  alone — unrelated applies (voice, messaging, nginx) stay non-disruptive.
- **D — wlan0/br-lan race removed** (`system/systemd/brlan-setup.service`). The
  fixed `sleep 2` is replaced by a bounded wait (≤15 s) until `iw dev wlan0 info`
  reports `type AP`, failing loudly on timeout, then enslaves wlan0 and runs
  `networkctl reconfigure br-lan`. networkd keeps sole `Bridge=br-lan` ownership
  in `30-wlan0.network` (hostapd has no `bridge=` line), as before.

**Tests:** `tests/test_render.py::test_mesh_up_is_rerunnable` (B);
`tests/test_apply_network_group.py` (C/D — reconfigure-not-reload, fixed restart
order, brlan-setup unit waits for AP mode). Full suite: 221 passed. Rendered
`nucleus-mesh-up.sh` passes `bash -n`; `brlan-setup.service` passes
`systemd-analyze verify`.

**Deployed:** installed to node 0042 and applied; verified live (brlan-setup ran
clean, wlan0 forwarding in br-lan, UI reachable).

## Architecture conformance (README)

- **One contract (§1):** `mesh.enabled` and `meshtastic.tx_enabled` live only in
  `schema.py`; API, CLI, apply and the boot unit consume them.
- **Pure schema / isolated I/O (§2):** rfkill/`/sys` access is in `rf.py`, not
  `schema.py`.
- **Config I/O (§1 debt):** boot unit + bridges load via `config.load()`.
- **Idempotent apply (§1):** reconciles reuse `_set_service()` and only act on a
  delta.
- **Imperative, not a rendered file:** rfkill + `lora.tx_enabled` are applied
  imperatively (like service enable/disable), documented — not hidden drift.
- **Outside the pipeline is intentional (§4):** the AP toggle is deliberately
  non-persistent, so it's live-only like Tailscale.
- **Repo ↔ live (§5):** `nucleus-rf.service` ships in `system/systemd/`,
  installed/enabled by `install.sh`.

## Risks

- **Bringing wlan1 back up** hits the known restart/ordering bugs in
  `network-apply-no-reboot.md` (brlan-setup race; `nucleus-mesh-up.sh` not safe
  to re-run). Prerequisite for clean live un-silence without a reboot.
- **Self-lockout:** turning off the AP/mesh can drop the operator's own link.
  UI must warn; USB-C RNDIS / eth0 / console are the safety net. The AP's
  reset-on-boot behaviour is itself a lockout recovery path.
- **`systemd-rfkill` restore** can re-block phy0 at boot — countered by the
  explicit unblock in `nucleus-rf.service`.

## Phases

0. ✅ **DONE** — B+C+D network-apply re-run fixes (see above). Implemented,
   tested (221 pass), deployed to node 0042 and verified live.
1. `schema.py` (`mesh.enabled`, `meshtastic.tx_enabled`) + `nucleusd/rf.py` +
   unit tests (real `/sys` + `rfkill list` fixtures; rfkill/systemctl mocked).
2. `nucleus-rf.service` + `install.sh` wiring (ordering vs systemd-rfkill).
3. `_reconcile_rf()` + `_reconcile_meshtastic_tx()` in `apply.py` + tests.
4. `radio_init.py` tx_enabled sync; bridge TX/heartbeat gating; `status.py`.
5. `POST /api/v1/rf/ap` endpoint + the `radios` page in `app.js` (toggles,
   lockout confirms, live status).
6. Docs: this file → implemented; README §1/§2/§4 note; manual page.

## Test plan

- Off-box `pytest`: schema defaults, phy resolution from fixtures, reconcile
  logic with mocked rfkill/systemctl, tx_enabled sync logic.
- On a spare node: wlan1 off → silent via `iw`/`rfkill`, survives reboot;
  Meshtastic off → `meshtastic --get lora.tx_enabled` false, still RX, survives
  reboot; AP off → silent, then ON again after reboot; USB-C/eth0 reachable
  throughout.
