# Nucleus V3 OS

A clean re-implementation of the Nucleus mesh-node OS. Design goal: a **single,
validated source of truth** for node configuration, driving every generated
system file, exposed through one API that serves both the self-hosted web UI and
any external app.

Target: Raspberry Pi (aarch64), Debian 13 (trixie), headless, 1 GB nodes —
memory is a first-class concern.

**Subsystems:** Wi-Fi 802.11s mesh (babeld L3 routing, smcroute multicast, HWMP
off), Meshtastic LoRa + ATAK CoT bridge, WiFi+LoRa text messaging, mesh PTT
voice, Reticulum (rnsd), nginx-fronted web UI, UFW host firewall. Optional per
node: Tailscale, official TAK Server (see §4).

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
   babeld.conf, smcroute.conf, hostapd.conf, meshtasticd config.yaml,
   ~natak/.reticulum/config, nginx vhost + htpasswd
        |
        v   (restart / enable / reload only what changed)
   networkd, nucleus-mesh, babeld, smcroute, hostapd, brlan-setup,
   meshtasticd, cot-bridge, rnsd, nginx
```

**Rules**

- **One contract.** `schema.py` is the only definition of a valid config. The
  API, CLI, renderer and every daemon consume it — no second place for rules or
  defaults to drift.
- **Operators set primitives; the schema derives the rest.** `node.id` is
  parsed from the hostname (`NNNN-nucleus`); from it the schema computes mesh
  IP, br-lan IP, AP SSID and IPv6 link-locals.
- **Templates are dumb.** They only substitute values from `render_context()`.
  All logic and validation live in Python, testable off-box.
- **Idempotent apply.** A file is written only if its content changed; a unit is
  touched only if one of its inputs changed. Optional services (meshtasticd,
  cot-bridge, rnsd) are enabled/disabled to match config. `nucleusctl apply`
  first normalizes `config.yaml` so keys added by a code update gain their
  schema defaults.

> **Known debt:** `messaging/service.py`, `voice/daemon.py`,
> `meshtastic/meshtastic_api.py` and `meshtastic/cot_bridge.py` were ported from
> V2 and still read `config.yaml` directly with their own defaults, bypassing
> `config.py`/`schema.py`. This violates the rules above and is logged to fix.
> New code must load config through `config.load()`.

---

## 2. Components (`nucleusd/`)

| Module | Responsibility |
|--------|----------------|
| `schema.py` | Config contract + derived values. Pure — no filesystem/hardware. |
| `config.py` | Load / atomically save / normalize `config.yaml`. The only module that should do config YAML I/O. |
| `apply.py` | Render → diff → restart. `TARGETS` maps each template to its dest + affected units. Shared by API and CLI. |
| `status.py` | Read-only runtime status: interfaces, unit states, Babel neighbours/routes (local-port 33123), mesh-node last-seen (5 s poll, lost nodes kept 15 min, in memory). |
| `api.py` | FastAPI app (`nucleusd`, :8080): REST API + web UI, mounts the routers below. |
| `cli.py` | `nucleusctl` — thin wrapper over the same modules. |
| `meshtastic/` | Radio configurator + router, first-boot radio init, CoT bridge daemon. |
| `messaging/` | Messaging daemon (WiFi mcast + LoRa → one deduped store) + router. |
| `voice/` | PTT voice daemon (OpenVLM hardware + browser soft PTT) + router. |
| `tailscale.py`, `tailscale_router.py` | Imperative Tailscale control (see §4). |
| `update.py` | Self-update: version check + launches `nucleus-update.sh`. |
| `templates/` | Jinja2 templates, named after their destination. |
| `web/` | Static UI (`index.html`, `cli.js`/`cli.css` shell, `voice.html`); talks only to `/api/v1/...`. |

Routers are thin HTTP layers: logic stays in the module/daemon they wrap.

**Core endpoints** (`/api/v1`) — full reference in [docs/API.md](docs/API.md):

- `GET /config` — current config + `_derived` (web password redacted)
- `PUT /config` — validate + persist (does **not** apply)
- `POST /apply?dry_run=<bool>` — render, write changed files, restart units
- `GET /status` — interfaces / services / Babel / mesh-node last-seen

`PUT` and `apply` are separate so a client can stage and dry-run first.
Cross-origin browser writes are refused (CSRF guard).

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
     `firewall.enabled: false` disables it). TAK/MediaMTX ports on eth0 are
     opened only if those packages are installed. nftables rules are untouched.
     See [Firewall](docs/manual/firewall.md).
3. **babeld** — unicast routes (mesh + br-lan subnets + default route).
4. **smcroute** — multicast between `wlan1` and `br-lan`.
5. **hostapd** — 5 GHz AP on `wlan0`.
6. **brlan-setup** — enslaves `wlan0` into `br-lan` (networkd can't while
   hostapd owns it). `apply` re-runs it whenever networkd or hostapd bounce.

nginx fronts the UI on :80/:443 (self-signed cert, `<hostname>.local` via avahi);
requests over eth0 need HTTP Basic auth, trusted nets (mesh, br-lan, Tailscale,
localhost) don't.

### Hard-won lessons (ported from Nucleus_OS — keep them)

- **No-echo multicast routing.** smcroute forwards only to the *other*
  interface. Echoing back minted new packets that bypassed 802.11s dedup →
  exponential amplification at 3+ nodes.
- **802.11s does multi-hop multicast natively** (RMC dedup), so `mesh_ttl` is
  the hop limit. Kernel default 31 is far too high; we use 8.
- **Multicast TTL bump on br-lan ingress.** ATAK sends TTL=1; nftables raises it
  to `mesh.mcast_ttl` on br-lan ingress only so it can be forwarded.

---

## 4. Outside the pipeline (intentional)

- **Tailscale** — needs a login link that connects back to Tailscale's servers,
  and its state lives in tailscaled, not `config.yaml`. Controlled live via
  `tailscale.py` / `/api/v1/tailscale`.
- **Official TAK Server (+ MediaMTX)** — **separate, uncommon install**; most
  nodes never run it, so it's kept out of `install.sh`/`apply`. Provisioned once
  with `sudo nucleus-tak-setup.sh` (needs the tak.gov `.deb`). It still reads
  its inputs from the `tak:` block via `nucleusctl tak-config`. See
  [takserver.md](docs/manual/takserver.md).

---

## 5. Repo ↔ live system

The **repo is the source of truth.** `install.sh` (idempotent) syncs it to the
node:

- apt packages, incl. meshtasticd (Meshtastic OBS repo) and Tailscale (its repo)
- Python package → `/opt/nucleus/venv`; `nucleusctl` + Reticulum CLIs →
  `/usr/local/bin`
- `system/` → systemd units, udev rule, NetworkManager conf, sudoers,
  `nucleus-update.sh`, `nucleus-tak-setup.sh` (inert unless used)
- GPS UART prep (boot config/cmdline)
- `config/config.yaml` → `/etc/nucleus/config.yaml` **only if absent**
- enables services, restarts the app daemons to pick up new code

Generated files are **artifacts** of `nucleusctl apply`, never hand-edited or
git-tracked in place. Workflow: edit repo → `install.sh` → `nucleusctl apply`.
Nodes self-update from the web UI (`nucleus-update.sh`: pull `main` → install →
apply → restart).

---

## 6. Configuration

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
web:
  password: "..."    # eth0 web login — CHANGE the default 52235223
```

Derived from id `9`: mesh IP `10.20.1.9`, br-lan IP `10.20.9.1`, AP SSID
`0009-nucleus-ap`, deterministic IPv6 link-locals.

Other sections: `br_lan`, `eth0`, `meshtastic`, `messaging`, `voice`,
`reticulum`, `web`, `firewall`, `tak`. Defaults and comments are in
[config/config.yaml](config/config.yaml); per-page detail in the
[manual](docs/manual/README.md).

```bash
sudo nucleusctl validate        # check config.yaml
sudo nucleusctl apply --dry-run # preview changed files + units
sudo nucleusctl apply           # render + restart affected units
nucleusctl status               # live status (JSON)
```

Or the web UI at `http://<hostname>.local` (fallback `http://<node-ip>:8080`) —
same operations, same API.

---

## 7. Directory layout

```
V3_OS/
├── nucleusd/            # Python package (see §2)
│   ├── meshtastic/  messaging/  voice/
│   ├── templates/       # Jinja2, named after destination files
│   └── web/             # static UI
├── system/              # static (non-rendered) system files
│   ├── systemd/         # nucleusd, nucleus-mesh, brlan-setup, cot-bridge,
│   │                    # nucleus-meshtastic-init, nucleus-messaging,
│   │                    # nucleus-voice, rnsd, takserver.service.d/
│   ├── bin/             # nucleus-update.sh, nucleus-tak-setup.sh
│   └── networkmanager/  sudoers.d/  udev/
├── config/config.yaml   # default config, seeded to /etc on install
├── docs/                # API.md + operator manual
├── tests/               # off-box tests
├── install.sh           # idempotent installer
├── pyproject.toml       # package + nucleusctl entry point
└── VERSION              # single version source
```

---

## 8. Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                          # no hardware needed
```

