# Nucleus V3 OS

A clean re-implementation of the Nucleus mesh-node OS. Design goal: a **single,
validated source of truth** for node configuration, driving every generated
system file, managed over ssh with `nucleusctl`.

Target: Raspberry Pi (aarch64), Debian 13 (trixie), headless, 1 GB nodes —
memory is a first-class concern.

**`drone` branch.** A trimmed build for airborne nodes: the Pi talks to a
flight controller over the GPIO UART, has no Meshtastic radio, and is meant to
move to a lower-powered board. Meshtastic/CoT bridge, messaging, voice,
Reticulum, Tailscale and TAK/OpenTAKServer are removed. Nodes on this branch
self-update from `drone`, not `main`.

**Subsystems:** Wi-Fi 802.11s mesh on `wlan1` (babeld L3 routing, smcroute
multicast, HWMP off), `wlan0` AP + `br-lan` for local access, UFW host firewall. The GPIO UART is kept free for the flight controller
(see §4). **mavlink-router** (prebuilt aarch64 binary) bridges the FC on
`/dev/serial0` @ 921600 to UDP 14550; the GCS connects to
`<mesh IP>:14550` (config: `system/mavlink-router/main.conf`).

---

## 1. Core logic — the config pipeline

Everything flows one direction, from one file:

```
/etc/nucleus/config.yaml         <- the ONLY thing an operator edits
        |
        v   (config.py: load + validate)
   schema.py : NucleusConfig     <- pydantic model; also DERIVES values
        |
        v   (render_context)
   apply.py  : Jinja2 templates  <- dumb substitution only
        |
        v   (write-if-changed)
   networkd/*.network, wpa_supplicant-mesh.conf, nucleus-mesh-up.sh,
   babeld.conf, smcroute.conf, hostapd.conf
        |
        v   (restart only what changed)
   networkd, nucleus-mesh, babeld, smcroute, hostapd, brlan-setup
```

**Rules**

- **One contract.** `schema.py` is the only definition of a valid config. The
  CLI, renderer and every daemon consume it — no second place for rules or
  defaults to drift.
- **Operators set primitives; the schema derives the rest.** `node.id` is
  parsed from the hostname (`NNNN-nucleus`); from it the schema computes mesh
  IP, br-lan IP, AP SSID and IPv6 link-locals.
- **Templates are dumb.** They only substitute values from `render_context()`.
  All logic and validation live in Python, testable off-box.
- **Idempotent apply.** A file is written only if its content changed; a unit is
  touched only if one of its inputs changed. `nucleusctl apply` first
  normalizes `config.yaml` so keys added by a code update gain their schema
  defaults and sections for removed subsystems are dropped. Config must be
  loaded through `config.load()`.

---

## 2. Components (`nucleusd/`)

| Module | Responsibility |
|--------|----------------|
| `schema.py` | Config contract + derived values. Pure — no filesystem/hardware. |
| `config.py` | Load / atomically save / normalize `config.yaml`. The only module that should do config YAML I/O. |
| `apply.py` | Render → diff → restart. `TARGETS` maps each template to its dest + affected units. |
| `status.py` | Read-only runtime status: interfaces, unit states, Babel neighbours/routes (local-port 33123). |
| `cli.py` | `nucleusctl` — thin wrapper over the same modules. |
| `templates/` | Jinja2 templates, named after their destination. |

---

## 3. Network stack

Bring-up is split between **declarative** (systemd-networkd) and **imperative**
(rendered shell script) work:

1. **systemd-networkd** — `br-lan` bridge, static addresses on `wlan1`/`br-lan`,
   `eth0` wiring (WAN DHCP client or LAN).
2. **`nucleus-mesh.service`** runs `nucleus-mesh-up.sh`:
   - `wlan1` into 802.11s mesh mode, SAE/WPA3 join (`wpa_supplicant-mesh.conf`),
   - **HWMP off** (`mesh_fwding=0`) — Babel owns all L3 routing,
   - `mesh_ttl`/`mesh_element_ttl` and RTS threshold,
   - nftables NAT (WAN mode) + multicast-TTL mangle rule,
   - UFW rebuilt every run (`ufw --force reset`, then rules from config;
     `firewall.enabled: false` disables it). nftables rules are untouched.
     See [Firewall](docs/manual/firewall.md).
3. **babeld** — unicast routes (mesh + br-lan subnets + default route).
4. **smcroute** — multicast between `wlan1` and `br-lan`.
5. **hostapd** — 5 GHz AP on `wlan0`.
6. **brlan-setup** — enslaves `wlan0` into `br-lan` (networkd can't while
   hostapd owns it). `apply` re-runs it whenever networkd or hostapd bounce.

### Hard-won lessons (ported from Nucleus_OS — keep them)

- **No-echo multicast routing.** smcroute forwards only to the *other*
  interface. Echoing back minted new packets that bypassed 802.11s dedup →
  exponential amplification at 3+ nodes.
- **802.11s does multi-hop multicast natively** (RMC dedup), so `mesh_ttl` is
  the hop limit. Kernel default 31 is far too high; we use 8.
- **Multicast TTL bump on br-lan ingress.** ATAK sends TTL=1; nftables raises it
  to `mesh.mcast_ttl` on br-lan ingress only so it can be forwarded.

---

## 4. Repo ↔ live system

The **repo is the source of truth.** `install.sh` (idempotent) syncs it to the
node:

- apt packages (Debian repos only)
- **Flight-controller UART prep:** `enable_uart=1`, serial console removed from
  the kernel cmdline, `serial-getty@ttyS0`/`ttyAMA0` masked, so nothing but the
  FC ever touches the GPIO UART (the cmdline change needs a reboot)
- Python package → `/opt/nucleus/venv`; `nucleusctl` → `/usr/local/bin`
- `system/` → systemd units, NetworkManager conf, `nucleus-update.sh`
- `config/config.yaml` → `/etc/nucleus/config.yaml` **only if absent**
- removes units/rules/launchers left by a full Nucleus build (Meshtastic,
  messaging, voice, Reticulum, TAK, Tailscale)
- enables services

Generated files are **artifacts** of `nucleusctl apply`, never hand-edited or
git-tracked in place. Workflow: edit repo → `install.sh` → `nucleusctl apply`.
Nodes self-update over ssh with `sudo /opt/nucleus/bin/nucleus-update.sh` (pull
`drone` → install → apply).

---

## 5. Configuration

Edit `/etc/nucleus/config.yaml`, then apply. A fresh node needs **no identity
edits** — `node.id` comes from the hostname (`0009-nucleus` → 9). Mostly just
set passwords:

```yaml
node:
  # id: 9            # optional override
mesh:
  password: "..."    # SAE/WPA3 (>= 8 chars)
ap:
  password: "..."    # AP WPA2
```

Derived from id `9`: mesh IP `10.20.1.9`, br-lan IP `10.20.9.1`, AP SSID
`0009-nucleus-ap`, deterministic IPv6 link-locals.

Other sections: `br_lan`, `eth0`, `firewall`. Defaults and comments are in
[config/config.yaml](config/config.yaml); more in the
[manual](docs/manual/README.md).

```bash
sudo nucleusctl validate        # check config.yaml
sudo nucleusctl apply --dry-run # preview changed files + units
sudo nucleusctl apply           # render + restart affected units
nucleusctl status               # live status (JSON)
```

---

## 6. Directory layout

```
V3_OS/
├── nucleusd/            # Python package (see §2)
│   └── templates/       # Jinja2, named after destination files
├── system/              # static (non-rendered) system files
│   ├── systemd/         # nucleus-mesh, brlan-setup
│   ├── bin/             # nucleus-update.sh
│   └── networkmanager/
├── config/config.yaml   # default config, seeded to /etc on install
├── docs/                # operator manual
├── tests/               # off-box tests
├── install.sh           # idempotent installer
├── pyproject.toml       # package + nucleusctl entry point
└── VERSION              # single version source
```

---

## 7. Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                          # no hardware needed
```

