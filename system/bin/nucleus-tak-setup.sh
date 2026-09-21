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
# fetched automatically) into ~natak, then:
#
#     sudo nucleus-tak-setup.sh [/path/to/takserver_*.deb]
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
if [ "$VARIANT" != "official" ]; then
    echo "tak.variant is '$VARIANT' (not 'official') in config.yaml — nothing to do."
    echo "Set 'tak: {variant: official}' and re-run."
    exit 0
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
if [ ! -d "$TAK" ]; then
    echo "==> install $(basename "$DEB")"
    case "$DEB" in
        /*) apt-get install -y "$DEB" ;;
        *)  apt-get install -y "./$DEB" ;;
    esac
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
else
    echo "==> PKI already present — skipping generation"
fi

# ---- 6. CoreConfig: truststore + auto-enrollment ----------------------------
CORE="$TAK/CoreConfig.example.xml"
echo "==> point truststore at intermediate CA"
sed -i "s/truststore-root/truststore-${INT_CA}/g" "$CORE"

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
else
    echo "==> certificateSigning already in CoreConfig — skipping"
fi

# ---- 7. Start the server; wait for the client TLS port to listen ------------
# On a Pi the Java/Ignite/Postgres stack can take many minutes to come up. Wait
# for TCP 8089 (client connector) to listen — the log has no reliable "started"
# string in 5.7 — then give a short settle before touching UserManager.
echo "==> enable + start takserver"
systemctl enable takserver.service
systemctl restart takserver.service
echo "    waiting for client port 8089 (up to 15 min)..."
UP=0
for i in $(seq 1 180); do
    if ss -tln 2>/dev/null | grep -q ':8089 '; then
        echo "    takserver up (8089 listening)"
        UP=1
        sleep 30   # settle: Ignite services register after the port opens
        break
    fi
    sleep 5
done
if [ "$UP" -ne 1 ]; then
    echo "WARNING: 8089 never came up — check $LOG; skipping admin cert" >&2
fi

# ---- 8. Admin certificate ---------------------------------------------------
if [ "$UP" -eq 1 ] && [ ! -f "$FILES/webadmin.p12" ]; then
    echo "==> create + authorize webadmin cert"
    sudo -u tak bash -c "
        set -e
        cd '$CERTS'
        ./makeCert.sh client webadmin
        java -jar '$TAK/utils/UserManager.jar' certmod -A '$FILES/webadmin.pem'
    "
else
    echo "==> webadmin cert already present — skipping"
fi

# ---- 9. Export webadmin.p12 + truststore for enrollment ---------------------
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

cat <<EOF

TAK Server provisioning complete.
  Web admin  : https://<node-ip>:8443  (import webadmin.p12 into your browser)
  Clients    : connect on port 8089 (TLS)
  Enrollment : ON — create users in the web UI, hand out
               truststore-${INT_CA}.p12 + username/password.
  Exported   : $EXPORT_HOME/webadmin.p12
               $EXPORT_HOME/truststore-${INT_CA}.p12
  Web UI     : same two files staged in $CERT_WEB_DIR for device download.
EOF
