# OpenTAKServer (OTS)

Optional [OpenTAKServer](https://github.com/brian7704/OpenTAKServer) on a node.
**Off by default — most nodes never run this.** It is the either/or alternative
to the [official TAK Server](takserver.md): a node runs one or the other, never
both (they conflict on PostgreSQL, MediaMTX, nginx and ports 8089/8443/8446).

OTS is installed with its own upstream installer, run by hand. Nucleus only
ships a small **fixup script** that moves two OTS nginx ports so OTS and the
Nucleus web UI can run side by side.

## Prerequisites

- The official TAK Server is **not** installed (`/opt/tak` absent).
- `tak.variant: opentakserver` in `/etc/nucleus/config.yaml`. On an existing
  node the `tak:` block may not exist (`install.sh` only seeds `config.yaml`
  when absent) — add it by hand:

  ```yaml
  tak:
    variant: opentakserver
  ```

  The other `tak.*` fields (`cert.*`, `keystore_pass`,
  `enrollment_validity_days`) apply to the official server only — OTS generates
  its own CA.

## Install

1. **SSH in as `natak`** (not root, not the web UI) and run the upstream
   installer:

   ```bash
   curl -s -L https://i.opentakserver.io/raspberry_pi_installer | bash -
   ```

   It refuses to run as root, uses `sudo` itself (you may be asked for the
   password), and asks questions on the terminal: **ZeroTier** (y/n),
   **Mumble Server** (y/n), and — on a re-run — the existing PostgreSQL `ots`
   password. It also runs `apt upgrade -y` on the whole system.

2. **Run the fixup** (the Nucleus web UI is down from the end of step 1 until
   this finishes — SSH is unaffected):

   ```bash
   sudo nucleus-ots-fixup.sh
   ```

## Why the fixup is needed

The upstream installer assumes it owns nginx:

| Installer action | Effect on Nucleus | Fixup |
|------------------|-------------------|-------|
| `rm -f /etc/nginx/sites-enabled/*` | Deletes the Nucleus web UI site link | `nucleusctl apply` re-creates it |
| `ots_http` listens on **8080** | 8080 is `nucleusd`; nginx can't start → Nucleus UI on 80/443 down | Moved to **8082** |
| `ots_https` UI listens on **443** | Nucleus owns 443 as `default_server`; OTS UI unreachable there | Moved to **8444** (incl. its `Host $host:443` headers) |
| `ots_certificate_enrollment` `/oauth` sends `Host $host:443` | OTS would point clients at 443 (Nucleus UI) | Header changed to **8444** |

Nothing is disabled — the ports are only moved. The script then runs
`nginx -t` and restarts nginx. It checks every edit landed and stops **before**
restarting nginx if the upstream file format has changed.

`nucleus-ots-fixup.sh` no-ops unless `tak.variant: opentakserver`, refuses if
the official server is installed, and is idempotent.

## Web UI page

When OTS is installed (`/etc/systemd/system/opentakserver.service` exists) the
Nucleus main menu shows **OPENTAKSERVER**. The page shows:

- **Server / status** — `OpenTAKServer` and the `opentakserver` service state
  (also on the System page's Services table).
- **Web UI link** — `https://<host>:8444`, built from the address used to reach
  the Nucleus UI, plus an **OPEN OPENTAKSERVER WEB UI** button.
- **Download truststore** — `truststore-root.p12`, staged in
  `/opt/nucleus/tak-certs/` by `nucleus-ots-fixup.sh`, for installing on client
  devices. The CA private key is never staged.

## Upgrading OTS

Upstream's upgrade path is re-running the same installer, which re-downloads
the nginx files and undoes the port moves. **After every OTS install or
upgrade, re-run `sudo nucleus-ots-fixup.sh`.**

## Ports

| Port | Service | Notes |
|------|---------|-------|
| 8444/tcp | OTS web UI (HTTPS) | Moved from 443 by the fixup |
| 8082/tcp | OTS plain HTTP UI + Marti API | Moved from 8080 by the fixup. Unauthenticated Marti access — see upstream warning in `ots_http` |
| 8443/tcp | Marti API | Client certificate required |
| 8446/tcp | Certificate enrollment | Username + password |
| 8089/tcp | TAK client TLS | |
| 8883/tcp | MQTT (RabbitMQ, via nginx TLS) | |
| 8322/tcp, 1936/tcp | RTSPS / RTMPS (MediaMTX, via nginx TLS) | |
| 80/tcp | OTS redirect to HTTPS | Nucleus answers first on 80; harmless |

All are reachable over the mesh, br-lan/AP and Tailscale (trusted
interfaces). On **eth0**, 8444/8443/8446/8089/8883/8322/1936 are opened
whenever `opentakserver.service` is enabled (checked by `nucleus-mesh-up.sh` on
every mesh bring-up, open to any source). **8082 is never opened on eth0.**

## Files

| Item | Location |
|------|----------|
| OTS config + data | `~natak/ots/` (`config.yml`, `logs/`, `ca/`) |
| OTS venv | `~natak/.opentakserver_venv/` |
| CA / server cert | `~natak/ots/ca/ca.pem`, `~natak/ots/ca/certs/opentakserver/` |
| MediaMTX | `~natak/ots/mediamtx/` (binary + `mediamtx.yml`), `mediamtx.service` |
| nginx | `/etc/nginx/sites-available/ots_*`, `/etc/nginx/streams-available/{rabbitmq,mediamtx}` |
| Services | `opentakserver`, `cot_parser`, `eud_handler`, `eud_handler_ssl`, `rabbitmq-server`, `postgresql`, `mediamtx` |

## Not yet integrated

Deferred until OTS has been seen running on a node:

- **Boot ordering** — no drop-in holding `opentakserver.service` until the mesh
  is up (the official server has one).
