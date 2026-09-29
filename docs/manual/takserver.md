# TAK Server (official)

Optional official TAK Server (tak.gov) + MediaMTX on a node. **Off by default —
most nodes never run this.** Not a web UI page: it is provisioned once by a
script, then administered from TAK's own web admin console.

## When to use

Run this only on a node that should host a full TAK Server (ATAK/WinTAK data
feeds, cert auto-enrollment, video via MediaMTX). Nodes without it pay no cost —
the boot-ordering drop-in and setup script are inert unless enabled.

## Prerequisites

- The TAK Server `.deb` from <https://tak.gov> (auth-walled — it cannot be
  downloaded automatically). Place it in `~natak`, e.g.
  `takserver_5.7-RELEASE32_all.deb`.
- The MediaMTX **linux_arm64** release tarball from
  <https://github.com/bluenviron/mediamtx/releases>, also in `~natak`, e.g.
  `mediamtx_v1.15.6_linux_arm64.tar.gz`. The script stops before changing
  anything if it can't find one (unless MediaMTX is already installed).
- `tak.variant: official` in `/etc/nucleus/config.yaml`. **On an existing node
  the `tak:` block does not exist in the file** — `install.sh` only seeds
  `config.yaml` when it is absent, so it never adds the block to a live node.
  You must add it by hand:

  ```yaml
  tak:
    variant: official
  ```

  All other `tak.*` fields fall back to schema defaults (see the table below),
  so only `variant` is required to enable setup.

## Configuration (`tak:` block)

Edit `/etc/nucleus/config.yaml`. This block is read by the setup script, **not**
by `nucleusctl apply` — installing the `.deb` and generating a PKI are
irreversible, per-node actions that don't belong in the idempotent apply loop.

| Field | Notes |
|-------|-------|
| `variant` | `none` (default), `official`, or `opentakserver` (either/or — see [OpenTAKServer](opentakserver.md)). This script no-ops unless `official`, and refuses to run if OpenTAKServer is installed. |
| `enrollment_validity_days` | Validity of certs issued via auto-enrollment (default 365). |
| `keystore_pass` | Signing keystore password (TAK default `atakatak`). |
| `cert.country` / `cert.state` / `cert.city` | X.509 C / ST / L. |
| `cert.organization` / `cert.organizational_unit` | X.509 O / OU. |

CA common names are **derived** from the hostname (no spaces): root
`<hostname>-root`, intermediate `<hostname>-ca` (e.g. `0042-nucleus-ca`).

## Setup

```bash
sudo nucleus-tak-setup.sh [/path/to/takserver_*.deb] [/path/to/mediamtx_*.tar.gz]
```

The script is not in your home directory — `install.sh` installs it to
`/opt/nucleus/bin/` and symlinks it onto `PATH` (`/usr/local/bin`), so run it
by name. With no arguments it auto-finds `~natak/takserver_*.deb` and
`~natak/mediamtx_*_linux_arm64.tar.gz`. The script is
idempotent — an existing PKI, package, or CoreConfig edit is detected and
skipped, so a re-run never regenerates a CA or clobbers a live server. Phases:

| Phase | Action |
|-------|--------|
| Config | Reads `tak:` block via `nucleusctl tak-config` (one validated source). |
| Prereqs | Adds bookworm repo (for `postgresql-15` + PostGIS), installs Java 17, raises nofile limits. |
| Install | `apt install ./takserver_*.deb` (apt resolves dependencies). |
| Cert metadata | Patches `cert-metadata.sh` with the `cert.*` values. |
| PKI | Generates root CA, intermediate CA, and the `takserver` server cert. |
| CoreConfig | Points the truststore at the intermediate CA and injects the certificate auto-enrollment block. |
| Admin cert | Creates the `webadmin` client cert (offline; skipped if it exists). |
| Start | Enables `takserver.service`; restarts it only if this run installed the package, generated PKI or edited CoreConfig, starts it if it is down, otherwise leaves it running. |
| Admin authorize | First install only: waits (up to 15 min) for port 8089, then authorizes `webadmin` as administrator. Re-runs skip this and do not wait. |
| Export | Copies `webadmin.p12` + the intermediate truststore to `~natak`, and stages the same two files in `/opt/nucleus/tak-certs/` (owned by `natak`) for the web UI to serve to connected devices. |
| Firewall | Opens the TAK ports on eth0 immediately (if UFW is active). See [Ports](#ports). |
| MediaMTX | Installs the binary + default config (if not installed), writes `mediamtx.service`, enables + starts it once (starts on boot), opens its ports on eth0. See [MediaMTX](#mediamtx). |

## After setup

1. Import `~natak/webadmin.p12` into your browser, open the admin console at
   `https://<node-ip>:8443`.
2. Create each end user and place them in the appropriate group.
3. Give the user the intermediate cert (`~natak/truststore-<hostname>-ca.p12`)
   plus their username/password.
   Both files are also staged in `/opt/nucleus/tak-certs/` so they can be
   downloaded to connected devices from the web UI.
4. On ATAK/WinTAK: install the intermediate cert, check **Enroll for Client
   Certificate** + **User Authentication**, and connect on port **8089**.
   (iTAK does not support this enrollment method.)

Client certificates are issued via enrollment — the script does **not**
pre-generate per-client certs. Revoke/inspect issued certs under
**Administrative → Client Certificates**; restart `takserver` after revoking.

## Web UI page

The **TAK SERVER** page appears in the Nucleus web UI on nodes where TAK Server
is installed (it shows "not installed" otherwise). It provides:

- **Status** — the `takserver` service state (also shown on the System page's
  Services table when installed).
- **Web admin pointer** — the TAK admin console URL (`https://<node-ip>:8443`,
  TAK's own port, not the Nucleus UI). Import `webadmin.p12` into your browser
  first, then open it to manage users and certificates.
- **Download certificates** — a button per staged cert
  (`/opt/nucleus/tak-certs/`): `webadmin.p12` and the intermediate truststore,
  for pulling onto connected devices.

## Ports

| Port | Service | Access control |
|------|---------|----------------|
| 8089/tcp | Client connect (TLS) | Client certificate |
| 8090/udp | QUIC | Client certificate |
| 8443/tcp | Web admin / WebTAK / API | Client certificate |
| 8446/tcp | Cert enrollment | Username + password only |

These ports are always reachable over the mesh, br-lan/AP and Tailscale (those
interfaces are fully trusted). On **eth0** they are opened automatically
whenever the `takserver` package is installed: `nucleus-mesh-up.sh` checks
`dpkg-query -W -f='${Status}' takserver` on every mesh bring-up, and the setup
script adds the same rules at the end of provisioning so they work immediately.
There is no config switch.

The eth0 rules accept **any source address**. Behind a NAT router that means
the local network only, but if eth0 sits on a network with public addresses
(including the node's public IPv6 addresses) the ports face the internet. 8446
is the weakest point — the intermediate truststore is public by design, so
enrollment is protected only by the TAK user's password. Use strong passwords
and disable users you don't need.

## MediaMTX

Media server for TAK video (RTSP/SRT), installed by the setup script:

| Item | Location |
|------|----------|
| Binary | `/usr/local/bin/mediamtx` |
| Config | `/usr/local/etc/mediamtx.yml` — the release's default, installed together with the binary; re-runs leave it alone while the binary is present, so your edits persist |
| Service | `/etc/systemd/system/mediamtx.service`, written by the setup script (only on TAK nodes). Runs as `natak`, after `nucleus-mesh.service`, restarts on failure, enabled on boot. Re-runs do not restart it. |

**Streams are open** — the default config allows anyone who can reach the ports
to publish and watch any path, with no username or password.

Opened on eth0 whenever `mediamtx.service` is enabled (checked by
`nucleus-mesh-up.sh` on every mesh bring-up), to any source address:

| Port | Use |
|------|-----|
| 8554/tcp | RTSP, e.g. `rtsp://<node-ip>:8554/<path>` |
| 8000/udp, 8001/udp | RTSP media over UDP (RTP/RTCP) |
| 8890/udp | SRT, e.g. `srt://<node-ip>:8890?streamid=read:<path>` |

MediaMTX's other protocols (RTMP 1935, HLS 8888, WebRTC 8889/8189) are on in the
default config and reachable over the mesh, AP and Tailscale, but are not
opened on eth0. To upgrade, remove `/usr/local/bin/mediamtx`, re-run the setup
script with the new tarball, then `sudo systemctl restart mediamtx`. This also
replaces `mediamtx.yml` with the new release's default — back it up first if
you edited it.

## Boot ordering

A `takserver.service.d/override.conf` drop-in orders TAK Server after
`nucleus-mesh.service` with a 30s settle delay, so its heavy Java/Postgres load
lands on an already-converged mesh instead of starving the timing-sensitive
mesh bring-up. The drop-in is inert on nodes where takserver is not installed.
