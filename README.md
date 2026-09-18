# Nucleus V3 OS

A clean re-implementation of the Nucleus mesh-node OS. The design goal is a
**single, validated source of truth** for node configuration, driving all
generated system files, exposed through one API that serves both a self-hosted
web UI and any external app.

Target platform: Raspberry Pi (aarch64), Debian 13 (trixie), headless. Runs on
1 GB production nodes; memory is a first-class concern.

> **Phase 1 scope (this repo so far):** Wi-Fi 802.11s mesh (babeld L3 routing,
> smcroute multicast bridging, HWMP disabled) + the configuration system (schema,
> API, CLI, web UI). Later phases (Meshtastic CoT bridge, PTT voice, Reticulum,
> etc.) plug into the same config/apply framework.

---

## 1. Core logic — the config pipeline

Everything flows one direction, from one file:

```
/etc/nucleus/config.yaml         <- the ONLY thing an operator edits
        |
        v   (load + validate)
   schema.py : NucleusConfig     <- pydantic model; also DERIVES values
        |
        v   (render_context)
   apply.py  : Jinja2 templates  <- dumb substitution only
        |
        v   (write-if-changed)
   /etc/babeld.conf, /etc/smcroute.conf, hostapd.conf,
   wpa_supplicant-mesh.conf, systemd-networkd/*.network,
   /opt/nucleus/bin/nucleus-mesh-up.sh
        |
        v   (restart only affected units)
   systemd-networkd, nucleus-mesh, babeld, smcroute, hostapd
```

**Why this shape**

- **One contract.** The pydantic model in `schema.py` is the single definition
  of what a valid node config is. The API, the CLI, and the renderer all import
  it, so there is no second place where rules can drift.
- **Operators set primitives; the system derives the rest.** Node identity comes
  from the hostname set at provisioning (`NNNN-nucleus`, so `node.id` is parsed
  automatically); from that id the schema computes `mesh_ip`, `br_lan_ip`, the AP
  SSID, and the per-interface IPv6 link-local addresses. The old `mesh.conf`
  required hand-pasting all of these per node — the main source of its bugs.
- **Templates are dumb.** They only substitute pre-computed values from
  `render_context()`. All logic and validation live in Python, testable off-box.
- **Idempotent apply.** `apply.py` writes a file only if its content changed and
  restarts a unit only if one of its inputs changed. Re-running with no config
  change touches nothing.

---

## 2. Component breakdown (`nucleusd/`)

| Module | Responsibility | Notes |
|--------|----------------|-------|
| `schema.py` | The config contract + derived values | Pure, no filesystem/hardware. Unit-testable. |
| `config.py` | Load / atomically save `config.yaml` | Only module that does YAML I/O. Atomic writes so a crash can't brick boot. |
| `apply.py` | Render templates → diff → restart units | The heart. `TARGETS` maps each template to its dest + the units it affects. Importable by both API and CLI. |
| `status.py` | Read-only runtime status | Babel neighbours (via local-port 33123), interface addrs, unit states. Never mutates. |
| `api.py` | FastAPI app: REST API + web UI | One always-on process. UI is static files calling the same API. |
| `cli.py` | `nucleusctl` | Thin wrapper over the same modules — identical behaviour to the API. |
| `templates/` | Jinja2 templates for every generated file | Named after their destination. |
| `web/` | Static web UI | `index.html` + `app.js`; talks only to `/api/v1/...`. |

**API endpoints** (`/api/v1`)

- `GET /config` — current config + `_derived` block
- `PUT /config` — validate + persist a replacement config (does **not** apply)
- `POST /apply?dry_run=<bool>` — render, write changed files, restart units
- `GET /status` — live interfaces / services / Babel neighbours

`PUT` and `apply` are deliberately separate so a client can stage config and
review a dry-run before committing.


---

## 3. Network stack logic

The mesh comes up in a deliberate order, split between **declarative**
(systemd-networkd) and **imperative** (a rendered shell script) work.

1. **systemd-networkd** owns what it can express declaratively: the `br-lan`
   bridge, static addresses on `wlan1`/`br-lan`, the `wlan0`→`br-lan` and
   `eth0`→bridge/DHCP wiring. No `sleep` hacks.
2. **`nucleus-mesh.service`** runs the rendered `nucleus-mesh-up.sh` for the
   imperative bits networkd can't do:
   - put `wlan1` into 802.11s mesh mode (`iw ... set type mesh`),
   - SAE/WPA3 join via a dedicated `wpa_supplicant-mesh.conf`,
   - **disable HWMP** (`mesh_fwding=0`) so **Babel owns all L3 routing** — the
     mesh does L2 transport only,
   - set the 802.11s `mesh_ttl`/`mesh_element_ttl` and RTS threshold,
   - install the NAT (WAN mode) and the multicast-TTL mangle rule via
     **nftables** (trixie's default; the old code used iptables).
3. **`babeld`** distributes unicast routes at L3 (mesh + br-lan subnets + default
   route for gateway sharing).
4. **`smcroute`** bridges multicast groups between `wlan1` and `br-lan`.
5. **`hostapd`** brings up the 5 GHz AP and, via its `bridge=br-lan` directive,
   enslaves `wlan0` itself — replacing the old `brlan-setup.service` sleep hack.

### Hard-won lessons carried over from Nucleus_OS

These are ported verbatim because they were field-debugged; the templates
preserve them:

- **No-echo multicast routing.** smcroute routes forward only to the *other*
  interface (`wlan1→br-lan`, `br-lan→wlan1`), never echoing back to the ingress
  interface. Echo routing caused exponential multicast amplification at 3+ nodes
  because each echo minted a new IP packet with a new mesh sequence number,
  bypassing 802.11s dedup.
- **802.11s does multi-hop multicast natively** at L2 with RMC dedup, so
  `mesh_ttl` (not smcroute echo) is the hop-limit knob. Kernel default 31 is far
  too high; we set 8.
- **Multicast TTL bump on br-lan ingress.** ATAK emits CoT/discovery/voice with
  TTL=1; the kernel won't forward multicast unless TTL>1, so an nftables mangle
  rule raises TTL to `mesh.mcast_ttl` on br-lan ingress only.

---

## 4. Repo ↔ live system model (Option A)

The **repo is the source of truth.** `install.sh` syncs it into the live system:

- Python package → `/opt/nucleus/venv` (installed via pip)
- `nucleusctl` launcher → `/usr/local/bin`
- static units → `/etc/systemd/system`, NM conf → `/etc/NetworkManager/conf.d`
- `config/config.yaml` → `/etc/nucleus/config.yaml` **only if absent** (never
  clobbers a live node's config)

Generated files (babeld.conf, smcroute.conf, etc.) are **artifacts**, not
hand-edited state — they're produced by `nucleusctl apply` from the one YAML, so
there's no need to git-track them in place. You edit in the repo, `install.sh`
to deploy code changes, and `nucleusctl apply` to regenerate system configs.

---

## 5. Configuration guide

Edit `/etc/nucleus/config.yaml`, then apply. A freshly flashed node needs **no
identity edits**: `node.id` is parsed from the system hostname (`NNNN-nucleus`,
e.g. `0009-nucleus` → 9), set when the SD card is flashed. You mostly just set
the passwords:

```yaml
node:
  # id: 9                    # optional; overrides the id parsed from the hostname
  # name:                    # optional hostname override
mesh:
  password: "..."            # SAE/WPA3 passphrase (>= 8 chars)
ap:
  password: "..."            # AP WPA2 passphrase
```

Derived automatically from the node id (e.g. `9`, whether parsed from the
hostname or set explicitly):

| Value | Result |
|-------|--------|
| mesh IP | `10.20.1.9` |
| br-lan IP | `10.20.9.1` |
| AP SSID | `0009-nucleus-ap` |
| IPv6 link-locals | deterministic per-interface `fe80::…` |

The hostname is the node's own identity (`0009-nucleus`), not a derived value —
it's the *source* of the id unless you override with `node.id` / `node.name`.

Workflow:

```bash
sudo nucleusctl validate        # check config.yaml
sudo nucleusctl apply --dry-run # preview changed files + units
sudo nucleusctl apply           # render + restart affected units
nucleusctl status               # live interfaces / services / babel neighbours
```

Or use the web UI at `http://<node-ip>:8080` (same operations, same API).

---

## 6. Directory layout

```
V3_OS/
├── nucleusd/                    # Python package
│   ├── schema.py                # config contract + derived values (pure)
│   ├── config.py                # YAML load/save (atomic)
│   ├── apply.py                 # render → diff → restart engine
│   ├── status.py                # read-only runtime status
│   ├── api.py                   # FastAPI: REST API + web UI mount
│   ├── cli.py                   # nucleusctl
│   ├── templates/               # Jinja2, named after destination files
│   └── web/                     # static UI (index.html, app.js)
├── system/                      # static (non-rendered) system files
│   ├── systemd/                 # nucleus-mesh.service, nucleusd.service
│   └── networkmanager/          # unmanaged-devices.conf
├── config/config.yaml           # default config, seeded to /etc on install
├── install.sh                   # idempotent installer (repo → live system)
├── pyproject.toml               # package + nucleusctl entry point
└── tests/                       # schema + render tests (run off-box)
```

---

## 7. Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                          # schema + template-render tests, no hardware needed
```
