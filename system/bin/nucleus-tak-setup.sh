#!/bin/bash
# ============================================================
# Nucleus V3 OS — official TAK Server (5.7) one-shot provisioner.
#
# NOT part of `nucleusctl apply`. Installing the tak.gov .deb and generating a
# PKI are irreversible, per-node-optional actions that don't belong in the
# idempotent apply loop (see README §4). Most nodes never run this. It reads its
# inputs from the ONE config.yaml, via `nucleusctl tak-config`, so there is still
# a single validated source of truth.
#
# Prereq: download takserver_*.deb from https://tak.gov (auth-walled — cannot be
# fetched automatically) and the MediaMTX linux_arm64 release tarball
# (https://github.com/bluenviron/mediamtx/releases) into ~natak, then:
#
#     sudo nucleus-tak-setup.sh [/path/to/takserver_*.deb] [/path/to/mediamtx_*.tar.gz]
#
# MediaMTX (RTSP/SRT video for TAK) is installed alongside TAK Server and
# enabled to start on boot.
#
# Idempotent: existing repo/PKI/CoreConfig edits are detected and skipped, so a
# re-run never regenerates a CA or clobbers a live server.
# ============================================================
set -euo pipefail

TAK=/opt/tak
CERTS="$TAK/certs"
FILES="$CERTS/files"
LOG="$TAK/logs/takserver-messaging.log"
EXPORT_USER=natak
EXPORT_HOME="/home/$EXPORT_USER"
# Web-UI-served copy: the download page points here so operators can pull the
# client certs onto connected devices (webadmin.p12 + intermediate truststore).
CERT_WEB_DIR=/opt/nucleus/tak-certs

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root (sudo nucleus-tak-setup.sh)" >&2
    exit 1
fi

# ---- 0. Read config through the schema (single source of truth) -------------
if ! command -v nucleusctl >/dev/null 2>&1; then
    echo "nucleusctl not found — run install.sh first" >&2
    exit 1
fi
CFG_JSON="$(nucleusctl tak-config)"
jget() { echo "$CFG_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }

VARIANT="$(jget "['variant']")"
if [ "$VARIANT" = "opentakserver" ]; then
    echo "tak.variant is 'opentakserver' — this script provisions the OFFICIAL server only."
    echo "OpenTAKServer is installed with its own installer; see docs/manual/opentakserver.md."
    exit 0
fi
if [ "$VARIANT" != "official" ]; then
    echo "tak.variant is '$VARIANT' (not 'official') in config.yaml — nothing to do."
    echo "Set 'tak: {variant: official}' and re-run."
    exit 0
fi

# Either/or: refuse if OpenTAKServer is already on this node (conflicting
# PostgreSQL, MediaMTX, nginx and ports 8089/8443/8446).
if [ -f /etc/systemd/system/opentakserver.service ]; then
    echo "OpenTAKServer is installed on this node (opentakserver.service exists)." >&2
    echo "Official TAK Server and OpenTAKServer are either/or — not installing." >&2
    exit 1
fi

C_COUNTRY="$(jget "['cert']['country']")"
C_STATE="$(jget "['cert']['state']")"
C_CITY="$(jget "['cert']['city']")"
C_ORG="$(jget "['cert']['organization']")"
C_OU="$(jget "['cert']['organizational_unit']")"
ROOT_CA="$(jget "['root_ca_name']")"
INT_CA="$(jget "['intermediate_ca_name']")"
KEYPASS="$(jget "['keystore_pass']")"
VALID_DAYS="$(jget "['enrollment_validity_days']")"

echo "==> TAK provisioning for $(jget "['hostname']")"
echo "    root CA=$ROOT_CA  intermediate CA=$INT_CA  validity=${VALID_DAYS}d"

# ---- 1. Locate the .deb -----------------------------------------------------
DEB="${1:-}"
if [ -z "$DEB" ]; then
    DEB="$(ls -1 "$EXPORT_HOME"/takserver_*.deb 2>/dev/null | head -n1 || true)"
fi
if [ -z "$DEB" ] || [ ! -f "$DEB" ]; then
    cat >&2 <<EOF
takserver .deb not found. Download it from https://tak.gov and re-run:
  sudo nucleus-tak-setup.sh /path/to/takserver_5.7-RELEASE32_all.deb
EOF
    exit 1
fi

# ---- 1b. Locate the MediaMTX tarball (checked up front, before any changes) --
MTX_BIN=/usr/local/bin/mediamtx
MTX_CONF=/usr/local/etc/mediamtx.yml
MTX_TGZ="${2:-}"
if [ -z "$MTX_TGZ" ]; then
    MTX_TGZ="$(ls -1 "$EXPORT_HOME"/mediamtx_*_linux_arm64.tar.gz 2>/dev/null | head -n1 || true)"
fi
if [ ! -x "$MTX_BIN" ] && { [ -z "$MTX_TGZ" ] || [ ! -f "$MTX_TGZ" ]; }; then
    cat >&2 <<EOF
MediaMTX tarball not found. Download the linux_arm64 release from
https://github.com/bluenviron/mediamtx/releases into $EXPORT_HOME and re-run:
  sudo nucleus-tak-setup.sh [takserver.deb] /path/to/mediamtx_vX.Y.Z_linux_arm64.tar.gz
EOF
    exit 1
fi

# ---- 2. Prerequisites (bookworm-pinned pg-15, java-17, nofile) --------------
if [ ! -f /etc/apt/sources.list.d/bookworm.list ]; then
    echo "==> add bookworm repo (postgresql-15 lives there on trixie)"
    printf 'Package: *\nPin: release n=bookworm\nPin-Priority: 100\n' \
        > /etc/apt/preferences.d/bookworm
    echo "deb http://deb.debian.org/debian bookworm main" \
        > /etc/apt/sources.list.d/bookworm.list
fi
echo "==> apt: postgresql-15 + postgis, openjdk-17"
apt-get update -qq
apt-get install -y postgresql-15 postgresql-client-15 postgresql-15-postgis-3 \
    openjdk-17-jdk openjdk-17-jre
if ! grep -q 'nofile      32768' /etc/security/limits.conf 2>/dev/null; then
    printf '*\tsoft\tnofile\t32768\n*\thard\tnofile\t32768\n' \
        >> /etc/security/limits.conf
fi

# ---- 3. Install TAK Server (apt resolves deps; needs ./ path) ---------------
# CHANGED=1 when this run installs/regenerates/edits anything TAK loads at
# start, so takserver is only restarted when it actually needs to be.
CHANGED=0
if [ ! -d "$TAK" ]; then
    echo "==> install $(basename "$DEB")"
    case "$DEB" in
        /*) apt-get install -y "$DEB" ;;
        *)  apt-get install -y "./$DEB" ;;
    esac
    CHANGED=1
else
    echo "==> $TAK exists — skipping .deb install"
fi


# ---- 4. Certificate metadata ------------------------------------------------
META="$CERTS/cert-metadata.sh"
echo "==> patch cert-metadata.sh"
sed -i \
    -e "s/^COUNTRY=.*/COUNTRY=$C_COUNTRY/" \
    -e "s/^STATE=.*/STATE=$C_STATE/" \
    -e "s/^CITY=.*/CITY=$C_CITY/" \
    -e "s/^ORGANIZATION=.*/ORGANIZATION=$C_ORG/" \
    -e "s/^ORGANIZATIONAL_UNIT=.*/ORGANIZATIONAL_UNIT=$C_OU/" \
    "$META"

# ---- 5. Build the PKI (as tak user; skip if already generated) --------------
if [ ! -f "$FILES/${INT_CA}-signing.jks" ]; then
    echo "==> generate PKI (root + intermediate + server certs)"
    # makeCert.sh ca prompts to move files into place — auto-answer 'y'.
    sudo -u tak bash -c "
        set -e
        cd '$CERTS'
        ./makeRootCa.sh --ca-name '$ROOT_CA'
        yes y | ./makeCert.sh ca '$INT_CA'
        ./makeCert.sh server takserver
    "
    CHANGED=1
else
    echo "==> PKI already present — skipping generation"
fi

# ---- 6. CoreConfig: truststore + auto-enrollment ----------------------------
CORE="$TAK/CoreConfig.example.xml"
if grep -q 'truststore-root' "$CORE"; then
    echo "==> point truststore at intermediate CA"
    sed -i "s/truststore-root/truststore-${INT_CA}/g" "$CORE"
    CHANGED=1
fi

# Re-run guard: skip only if an ACTIVE (uncommented) certificateSigning block
# exists. The example file ships a commented-out sample of this block, so a
# plain grep for the tag matches it and would wrongly skip insertion.
if ! python3 - "$CORE" <<'PY'
import re, sys
xml = re.sub(r"<!--.*?-->", "", open(sys.argv[1]).read(), flags=re.S)
sys.exit(0 if "<certificateSigning" in xml else 1)
PY
then
    echo "==> enable certificate auto-enrollment in CoreConfig"
    # Insert the certificateSigning block (equivalent to the UI's "Enable
    # Certificate Enrollment" -> TAK Server CA) before </Configuration>.
    BLOCK=$(cat <<EOF
    <certificateSigning CA="TAKServer">
        <certificateConfig>
            <nameEntries>
                <nameEntry name="O" value="$C_ORG"/>
                <nameEntry name="OU" value="$C_OU"/>
            </nameEntries>
        </certificateConfig>
        <TAKServerCAConfig keystore="JKS" keystoreFile="certs/files/${INT_CA}-signing.jks" keystorePass="$KEYPASS" validityDays="$VALID_DAYS" signatureAlg="SHA256WithRSA"/>
    </certificateSigning>
EOF
)
    python3 - "$CORE" "$BLOCK" <<'PY'
import sys
path, block = sys.argv[1], sys.argv[2]
with open(path) as f:
    xml = f.read()
xml = xml.replace("</Configuration>", block + "\n</Configuration>", 1)
with open(path, "w") as f:
    f.write(xml)
PY
    CHANGED=1
else
    echo "==> certificateSigning already in CoreConfig — skipping"
fi

# ---- 7. webadmin client cert (offline — does not need takserver running) ----
NEW_ADMIN=0
if [ ! -f "$FILES/webadmin.p12" ]; then
    echo "==> create webadmin cert"
    sudo -u tak bash -c "cd '$CERTS' && ./makeCert.sh client webadmin"
    NEW_ADMIN=1
else
    echo "==> webadmin cert already present — skipping"
fi

# ---- 8. Start takserver only if this run changed something or it's down -----
systemctl enable takserver.service
if [ "$CHANGED" -eq 1 ]; then
    echo "==> restart takserver (config/PKI/package changed)"
    systemctl restart takserver.service
elif ! systemctl is-active --quiet takserver.service; then
    echo "==> start takserver"
    systemctl start takserver.service
else
    echo "==> takserver running, nothing changed — no restart"
fi

# ---- 9. Authorize webadmin (first install only; needs takserver up) ---------
# UserManager certmod talks to the running server. On a Pi the Java/Ignite/
# Postgres stack can take many minutes to come up; TCP 8089 listening is the
# readiness signal (5.7's log has no reliable "started" line), then a short
# settle for Ignite services to register.
if [ "$NEW_ADMIN" -eq 1 ]; then
    echo "    waiting for client port 8089 (up to 15 min) to authorize webadmin..."
    UP=0
    for i in $(seq 1 180); do
        if ss -tln 2>/dev/null | grep -q ':8089 '; then
            echo "    takserver up (8089 listening)"
            UP=1
            sleep 30
            break
        fi
        sleep 5
    done
    if [ "$UP" -eq 1 ]; then
        echo "==> authorize webadmin as administrator"
        sudo -u tak java -jar "$TAK/utils/UserManager.jar" certmod -A "$FILES/webadmin.pem"
    else
        echo "WARNING: 8089 never came up — check $LOG. Authorize later with:" >&2
        echo "  sudo -u tak java -jar $TAK/utils/UserManager.jar certmod -A $FILES/webadmin.pem" >&2
    fi
fi

# ---- 10. Export webadmin.p12 + truststore for enrollment --------------------
echo "==> export credentials to $EXPORT_HOME"
cp -v "$FILES/webadmin.p12" "$EXPORT_HOME/"
cp -v "$FILES/truststore-${INT_CA}.p12" "$EXPORT_HOME/"
chown "$EXPORT_USER:$EXPORT_USER" \
    "$EXPORT_HOME/webadmin.p12" "$EXPORT_HOME/truststore-${INT_CA}.p12"

# Also stage them where the web UI serves downloads to connected devices.
echo "==> stage certs for web UI download ($CERT_WEB_DIR)"
mkdir -p "$CERT_WEB_DIR"
cp -v "$FILES/webadmin.p12" "$CERT_WEB_DIR/"
cp -v "$FILES/truststore-${INT_CA}.p12" "$CERT_WEB_DIR/"
chown -R "$EXPORT_USER:$EXPORT_USER" "$CERT_WEB_DIR"

# ---- 11. Open TAK ports on eth0 now -----------------------------------------
# nucleus-mesh-up.sh re-adds these on every mesh bring-up (it detects the
# installed takserver package), so this only covers the time until the next
# boot/apply. ufw skips rules that already exist, so re-runs are harmless.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    echo "==> open TAK ports on eth0 (8443/8089/8446 tcp, 8090 udp)"
    ufw allow in on eth0 to any port 8443 proto tcp comment 'tak web admin'
    ufw allow in on eth0 to any port 8089 proto tcp comment 'tak client tls'
    ufw allow in on eth0 to any port 8446 proto tcp comment 'tak cert enrollment'
    ufw allow in on eth0 to any port 8090 proto udp comment 'tak quic'
fi

# ---- 12. MediaMTX (RTSP/SRT video) ------------------------------------------
# Binary + default config from the release tarball (default config used as-is:
# open publish/read, no auth; left alone once installed).
if [ ! -x "$MTX_BIN" ]; then
    echo "==> install MediaMTX from $(basename "$MTX_TGZ")"
    tar -xzf "$MTX_TGZ" -C "$(dirname "$MTX_BIN")" mediamtx
    tar -xzf "$MTX_TGZ" -C "$(dirname "$MTX_CONF")" mediamtx.yml
else
    echo "==> $MTX_BIN exists — skipping MediaMTX install"
fi
echo "==> mediamtx.service (enabled on boot)"
cat > /etc/systemd/system/mediamtx.service <<EOF
[Unit]
Description=MediaMTX media server (RTSP/SRT video for TAK)
After=nucleus-mesh.service network-online.target
Wants=nucleus-mesh.service network-online.target

[Service]
User=$EXPORT_USER
ExecStart=$MTX_BIN $MTX_CONF
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now mediamtx.service

# Open MediaMTX ports on eth0 now (RTSP + SRT). nucleus-mesh-up.sh re-adds
# these on every mesh bring-up while mediamtx.service is enabled.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    echo "==> open MediaMTX ports on eth0 (8554 tcp, 8000/8001/8890 udp)"
    ufw allow in on eth0 to any port 8554 proto tcp comment 'mediamtx rtsp'
    ufw allow in on eth0 to any port 8000 proto udp comment 'mediamtx rtp'
    ufw allow in on eth0 to any port 8001 proto udp comment 'mediamtx rtcp'
    ufw allow in on eth0 to any port 8890 proto udp comment 'mediamtx srt'
fi

cat <<EOF

TAK Server provisioning complete.
  Web admin  : https://<node-ip>:8443  (import webadmin.p12 into your browser)
  Clients    : connect on port 8089 (TLS)
  Enrollment : ON — create users in the web UI, hand out
               truststore-${INT_CA}.p12 + username/password.
  Exported   : $EXPORT_HOME/webadmin.p12
               $EXPORT_HOME/truststore-${INT_CA}.p12
  Web UI     : same two files staged in $CERT_WEB_DIR for device download.
  MediaMTX   : rtsp://<node-ip>:8554/<path>  srt://<node-ip>:8890?streamid=publish:<path>
               open publish/read (no auth); config $MTX_CONF; starts on boot.
EOF
