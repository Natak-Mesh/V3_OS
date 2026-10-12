# RF control (radio kill switch)

Status: PLANNED — not implemented. Work is on branch `rfkill`, NOT `main`.
Bench-test on a spare node before merging.

## Problem / goal

We want to take a node RF-silent without shutting it down, so a wired link
(USB-C RNDIS, eth0) keeps working while the radios stop transmitting. We also
want the in-between states, not just all-on/all-off:

- **Full RF silence** — no Wi-Fi mesh, no AP, no LoRa. Reach the node only over
  USB-C RNDIS, eth0 or the console.
- **LoRa only** — Meshtastic (and optionally RNode) up, both Wi-Fi radios off.
- **Wi-Fi only** — mesh + AP up, LoRa off.
- **Wi-Fi + LoRa** — the normal all-on state.

The chosen state must **persist across reboots** (operator decision): it lives
in `config.yaml` and is re-enforced on every boot, before the radio services
start.

## Current state (live node, 0042-nucleus)

| Radio | Interface / phy | Controlled today by | Can go RF-silent now? |
|-------|-----------------|---------------------|-----------------------|
| Wi-Fi mesh | wlan1 / phy1 (USB Alfa) | nothing — `nucleus-mesh`, `babeld`, `smcroute` always run | No |
| Wi-Fi AP | wlan0 / phy0 (onboard, 31 dBm) | nothing — `hostapd` always runs | No |
| Meshtastic LoRa | SX1262 (RAK13302) | `meshtastic.enabled` → stops `meshtasticd` | Yes (also stops RX + CoT bridge) |
| Reticulum RNode | RAK4631 USB | `reticulum.rnode_enabled` | Yes |
| Bluetooth | hci0 | already soft-blocked by `rfkill` | Yes (already off) |

So LoRa and RNode have per-radio switches; the two Wi-Fi radios have none, and
there is no single "go silent" control.

## Design

Follows the README §1 pipeline: one contract in `schema.py`, hardware access
isolated, idempotent apply, state driven from `config.yaml`.

### 1. Config contract — `schema.py`

Add an `rf:` block:

```yaml
rf:
  silent: false   # master kill switch; true = all radios off
  mesh:  true     # Wi-Fi 802.11s mesh (wlan1)
  ap:    true     # Wi-Fi AP (wlan0)
  lora:  true     # Meshtastic SX1262
  rnode: true     # Reticulum RNode
```

To avoid a second source of truth (README §1, "one contract"), `schema.py`
**derives** the effective per-radio state and nothing else reads the raw flags:

- `rf.silent: true` forces every radio off regardless of the per-radio flags.
- Effective LoRa on = `rf.lora and not rf.silent and meshtastic.enabled`.
- Effective RNode on = `rf.rnode and not rf.silent and reticulum.rnode_enabled`.
- Effective mesh/AP on = the flag and not silent.

`render_context()` exposes only the derived values (`rf_mesh_on`, `rf_ap_on`,
`rf_lora_on`, `rf_rnode_on`). Validation rejects nonsense (e.g. `rf.lora: true`
while `meshtastic.enabled: false`) with a clear message rather than silently
ignoring it. `schema.py` stays pure — no `/sys`, no `rfkill`.

### 2. Hardware module — `nucleusd/rf.py`

The only place that touches rfkill. Mirrors how `status.py` isolates runtime I/O.

- Resolve a phy from its interface name via `/sys/class/net/<if>/phy80211`
  (symlink → `phyN`), **not** a fixed `rfkill` index — indices and USB port
  paths are not stable.
- `block(phy)` / `unblock(phy)` wrap `rfkill block|unblock <phy>`.
- A helper maps the derived RF state to the set of phys to block/unblock.

### 3. Persistence — `system/systemd/nucleus-rf.service`

A oneshot that runs **before** `nucleus-mesh`, `hostapd` and `meshtasticd` and
calls `python -m nucleusd.rf` to enforce the config's RF state on every boot.
This is what makes the setting survive reboots.

We do **not** rely on `systemd-rfkill`'s saved state: it keys saved state by USB
port path (e.g. `...usb-0:1.2:1.0:wlan`), so moving the Alfa to another USB port
loses it. Enforcing from `config.yaml` is deterministic.

### 4. Apply reconcile — `apply.py`

Add `_reconcile_rf()`, called every apply (cheap, like the meshtastic/reticulum
reconciles already there):

- Apply rfkill live via `nucleusd.rf` to match the derived state.
- Use the existing `_set_service()` to enable/disable the units tied to each
  radio:
  - mesh off → `nucleus-mesh`, `babeld`, `smcroute`
  - AP off → `hostapd`, and re-run `brlan-setup` when it comes back
  - LoRa off → `meshtasticd`, `nucleus-meshtastic-init`, `cot-bridge`
  - RNode off → forced false in the rendered Reticulum config
- Only acts when live state differs from config (idempotent).

### 5. Daemon tolerance + status

- `status.py` reports per-radio RF state (derived state + live `rfkill`).
- Messaging and voice Wi-Fi lanes and `babeld` must tolerate wlan1 being down
  without crash-looping.

### 6. API / UI

The existing config + `POST /apply` flow already carries `rf.*` (one contract).
UI gets an "RF silent" master toggle plus per-radio switches, and a warning
before turning off the AP/mesh if that is the session's only link.

## Architecture conformance (README)

- **One contract (§1):** `rf:` lives only in `schema.py`; it derives effective
  radio state. API, CLI, `apply` and the boot unit consume that — no second
  place for the rules.
- **Avoids duplicate truth:** `rf.lora`/`rf.rnode` overlap with
  `meshtastic.enabled`/`reticulum.rnode_enabled`; resolved by having `schema.py`
  compute one effective value that everything else reads.
- **Config I/O (§1 known debt):** the boot unit loads via `config.load()`, never
  reading YAML directly.
- **Pure schema / isolated I/O (§2):** hardware access is in `rf.py`, not
  `schema.py`.
- **Idempotent apply (§1):** `_reconcile_rf()` reuses `_set_service()` and only
  changes what differs.
- **Imperative, not a rendered file:** rfkill state is applied imperatively
  (like service enable/disable), not written as a generated file. README §1/§2
  get a note so this is documented, not hidden drift.
- **Repo ↔ live (§5):** `nucleus-rf.service` ships in `system/systemd/` and is
  installed/enabled by `install.sh`.

## Risks

- **Bringing radios back up** hits the known restart/ordering bugs in
  `network-apply-no-reboot.md` (brlan-setup race; `nucleus-mesh-up.sh` not safe
  to re-run). That work is effectively a prerequisite for a clean live un-silence
  without a reboot.
- **Self-lockout:** turning off the AP/mesh can drop the very link the operator
  is on. UI must warn; USB-C RNDIS / eth0 / console remain the safety net.
- **RNode/meshtastic flag overlap** must be collapsed to one derived value or we
  reintroduce config drift.

## Phases

1. `schema.py` `rf:` block + derivation + validation; `nucleusd/rf.py`; unit
   tests (real `/sys` + `rfkill list` fixtures, rfkill/systemctl mocked).
2. `nucleus-rf.service` + `install.sh` wiring.
3. `_reconcile_rf()` in `apply.py` + tests.
4. Daemon tolerance for wlan1 down + `status.py` RF state.
5. API confirmation + UI toggles/warnings.
6. Docs: this file → implemented; README §1/§2 note; a manual page.

## Test plan

- Off-box `pytest`: schema validation matrix, phy resolution from fixtures,
  reconcile logic with mocked rfkill/systemctl.
- On a spare node: for each state confirm silence via `iw dev`, `rfkill list`,
  `meshtastic --info`; confirm USB-C/eth0 still reachable; confirm the state
  survives a reboot.

## Open questions

- Add a LoRa **receive-only** mode (`lora.tx_enabled: false` via `radio_init`)
  vs. only full-stop (`meshtasticd` down)?
- Is the RNode in scope for v1, or Wi-Fi + Meshtastic only?
- Should the UI hard-block (not just warn) turning off the session's only link?

