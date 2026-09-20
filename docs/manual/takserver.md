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
- `tak.variant: official` set in `config.yaml` (see below).

## Configuration (`tak:` block)

Edit `/etc/nucleus/config.yaml`. This block is read by the setup script, **not**
by `nucleusctl apply` — installing the `.deb` and generating a PKI are
irreversible, per-node actions that don't belong in the idempotent apply loop.

| Field | Notes |
|-------|-------|
| `variant` | `none` (default) or `official`. Setup no-ops unless `official`. |
| `enrollment_validity_days` | Validity of certs issued via auto-enrollment (default 365). |
| `keystore_pass` | Signing keystore password (TAK default `atakatak`). |
| `cert.country` / `cert.state` / `cert.city` | X.509 C / ST / L. |
| `cert.organization` / `cert.organizational_unit` | X.509 O / OU. |

CA common names are **derived** from the hostname (no spaces): root
`<hostname>-root`, intermediate `<hostname>-ca` (e.g. `0042-nucleus-ca`).

## Setup

```bash
sudo nucleus-tak-setup.sh [/path/to/takserver_*.deb]
```

With no argument it auto-finds `~natak/takserver_*.deb`. The script is
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
| Start | Enables + starts `takserver.service`, waits for a clean messaging-server start. |
| Admin | Creates the `webadmin` client cert and authorizes it as administrator. |
| Export | Copies `webadmin.p12` + the intermediate truststore to `~natak`. |

## After setup

1. Import `~natak/webadmin.p12` into your browser, open the admin console at
   `https://<node-ip>:8443`.
2. Create each end user and place them in the appropriate group.
3. Give the user the intermediate cert (`~natak/truststore-<hostname>-ca.p12`)
   plus their username/password.
4. On ATAK/WinTAK: install the intermediate cert, check **Enroll for Client
   Certificate** + **User Authentication**, and connect on port **8089**.
   (iTAK does not support this enrollment method.)

Client certificates are issued via enrollment — the script does **not**
pre-generate per-client certs. Revoke/inspect issued certs under
**Administrative → Client Certificates**; restart `takserver` after revoking.

## Ports

| Port | Service |
|------|---------|
| 8089/tcp | Client connect (TLS) |
| 8090/udp | QUIC |
| 8443/tcp | Web admin / WebTAK / API |
| 8446/tcp | Cert enrollment |

## Boot ordering

A `takserver.service.d/override.conf` drop-in orders TAK Server after
`nucleus-mesh.service` with a 30s settle delay, so its heavy Java/Postgres load
lands on an already-converged mesh instead of starving the timing-sensitive
mesh bring-up. The drop-in is inert on nodes where takserver is not installed.
