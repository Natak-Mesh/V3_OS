#!/bin/bash
# ============================================================
# Nucleus V3 OS — OpenTAKServer (OTS) post-install fixup.
#
# NOT part of `nucleusctl apply`, and NOT an installer. OTS is installed
# manually, as natak over SSH (it refuses root and prompts on /dev/tty):
#
#     curl -s -L https://i.opentakserver.io/raspberry_pi_installer | bash -
#
# then this script is run to make OTS coexist with the Nucleus stack:
#
#     sudo nucleus-ots-fixup.sh
#
# Why it's needed — the upstream installer:
#   - deletes EVERY link in /etc/nginx/sites-enabled/ (incl. the Nucleus UI);
#   - enables ots_http on 8080, which nucleusd (uvicorn) already owns, so
#     nginx fails to start and the Nucleus web UI on 80/443 goes down;
#   - serves the OTS web UI on 443, which the Nucleus vhost owns as
#     default_server, so the OTS UI is unreachable there.
#
# Fixes (nothing is disabled — ports are moved):
#   1. ots_http : 8080 -> 8082   (OTS plain-HTTP UI + Marti API)
#   2. ots_https: 443  -> 8444   (OTS web UI), incl. the `Host $host:443` headers
#      ots_certificate_enrollment: `Host $host:443` headers -> 8444
#   3. `nucleusctl apply` re-creates the Nucleus nginx site link
#   4. nginx -t, then restart nginx
#   5. stage ~natak/ots/ca/truststore-root.p12 in /opt/nucleus/tak-certs
#      (downloaded from the Nucleus web UI OPENTAKSERVER page)
#
# The upstream installer is also OTS's upgrade path and re-downloads the nginx
# files every run, so RE-RUN THIS SCRIPT AFTER EVERY OTS INSTALL/UPGRADE.
# Idempotent: already-moved ports are left alone.
# ============================================================
set -euo pipefail

OTS_HTTP_PORT=8082
OTS_HTTPS_PORT=8444

SITES=/etc/nginx/sites-available
OTS_HTTP="$SITES/ots_http"
OTS_HTTPS="$SITES/ots_https"
OTS_ENROLL="$SITES/ots_certificate_enrollment"

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root (sudo nucleus-ots-fixup.sh)" >&2
    exit 1
fi

# ---- 0. Config + either/or guards -------------------------------------------
if ! command -v nucleusctl >/dev/null 2>&1; then
    echo "nucleusctl not found — run install.sh first" >&2
    exit 1
fi
VARIANT="$(nucleusctl tak-config | python3 -c "import sys,json;print(json.load(sys.stdin)['variant'])")"
if [ "$VARIANT" != "opentakserver" ]; then
    echo "tak.variant is '$VARIANT' (not 'opentakserver') in config.yaml — nothing to do."
    echo "Set 'tak: {variant: opentakserver}' and re-run."
    exit 0
fi
if [ -d /opt/tak ]; then
    echo "Official TAK Server is installed on this node (/opt/tak exists)." >&2
    echo "Official TAK Server and OpenTAKServer are either/or — not continuing." >&2
    exit 1
fi
for f in "$OTS_HTTP" "$OTS_HTTPS" "$OTS_ENROLL"; do
    if [ ! -f "$f" ]; then
        echo "$f not found — run the OpenTAKServer installer first (as natak):" >&2
        echo "  curl -s -L https://i.opentakserver.io/raspberry_pi_installer | bash -" >&2
        exit 1
    fi
done

# ---- 1. ots_http: 8080 -> 8082 ----------------------------------------------
# Only `listen` lines are touched (IPv4 + IPv6). Port 80 (redirect-only) stays.
echo "==> ots_http: listen 8080 -> $OTS_HTTP_PORT"
sed -i -E "s/^([[:space:]]*listen[[:space:]]+(\[::\]:)?)8080([[:space:];])/\1${OTS_HTTP_PORT}\3/" "$OTS_HTTP"

# ---- 2. ots_https: 443 -> 8444 ----------------------------------------------
# The UI server block's listen line, plus the Host headers that hard-code 443
# (OTS uses them to build URLs back to its UI). The 8443 block is untouched.
echo "==> ots_https: listen 443 -> $OTS_HTTPS_PORT (+ Host \$host:443 headers)"
sed -i -E \
    -e "s/^([[:space:]]*listen[[:space:]]+(\[::\]:)?)443([[:space:];])/\1${OTS_HTTPS_PORT}\3/" \
    -e "s/\\\$host:443;/\$host:${OTS_HTTPS_PORT};/g" \
    "$OTS_HTTPS"

# ---- 2b. ots_certificate_enrollment: Host $host:443 -> 8444 -----------------
# Its /oauth block tells OTS the UI is on 443; point it at the moved UI port.
# The 8446 listen line is untouched.
echo "==> ots_certificate_enrollment: Host \$host:443 headers -> $OTS_HTTPS_PORT"
sed -i -e "s/\\\$host:443;/\$host:${OTS_HTTPS_PORT};/g" "$OTS_ENROLL"

# ---- Verify the edits landed (catches an upstream format change) ------------
FAIL=0
for f in "$OTS_HTTPS" "$OTS_ENROLL"; do
    if grep -q '\$host:443;' "$f"; then
        echo "ERROR: $f still has Host \$host:443" >&2; FAIL=1
    fi
done
if grep -Eq '^[[:space:]]*listen[[:space:]]+(\[::\]:)?8080([[:space:];])' "$OTS_HTTP"; then
    echo "ERROR: $OTS_HTTP still listens on 8080" >&2; FAIL=1
fi
if ! grep -Eq "^[[:space:]]*listen[[:space:]]+(\[::\]:)?${OTS_HTTP_PORT}([[:space:];])" "$OTS_HTTP"; then
    echo "ERROR: $OTS_HTTP has no listen $OTS_HTTP_PORT (upstream file format changed?)" >&2; FAIL=1
fi
if grep -Eq '^[[:space:]]*listen[[:space:]]+(\[::\]:)?443([[:space:];])' "$OTS_HTTPS"; then
    echo "ERROR: $OTS_HTTPS still listens on 443" >&2; FAIL=1
fi
if ! grep -Eq "^[[:space:]]*listen[[:space:]]+(\[::\]:)?${OTS_HTTPS_PORT}([[:space:];])" "$OTS_HTTPS"; then
    echo "ERROR: $OTS_HTTPS has no listen $OTS_HTTPS_PORT (upstream file format changed?)" >&2; FAIL=1
fi
if [ "$FAIL" -ne 0 ]; then
    echo "Port fixes did not apply cleanly — nginx NOT restarted. Inspect the files above." >&2
    exit 1
fi

# ---- 3. Restore the Nucleus nginx site --------------------------------------
# The OTS installer ran `rm -f /etc/nginx/sites-enabled/*`. apply re-creates
# the Nucleus link (and only removes the stock `default` site — the ots_* links
# are left alone).
echo "==> nucleusctl apply (restores the Nucleus nginx site link)"
nucleusctl apply

# ---- 4. Validate + restart nginx --------------------------------------------
# restart (not reload): after the OTS installer nginx is usually in a failed
# state (8080 bind clash), and reload does nothing on a stopped unit.
echo "==> nginx -t"
if ! nginx -t; then
    echo "nginx config test failed — nginx NOT restarted. Fix the error above and re-run." >&2
    exit 1
fi
echo "==> restart nginx"
systemctl restart nginx

# ---- 5. Stage the truststore for web UI download ----------------------------
# The Nucleus TAK page serves *.p12 from CERT_WEB_DIR. Only the public
# truststore is copied — never ca-do-not-share.key.
OTS_TRUSTSTORE=/home/natak/ots/ca/truststore-root.p12
CERT_WEB_DIR=/opt/nucleus/tak-certs
if [ -f "$OTS_TRUSTSTORE" ]; then
    echo "==> stage truststore for web UI download ($CERT_WEB_DIR)"
    mkdir -p "$CERT_WEB_DIR"
    install -m 644 -o natak -g natak "$OTS_TRUSTSTORE" "$CERT_WEB_DIR/truststore-root.p12"
    chown natak:natak "$CERT_WEB_DIR"
else
    echo "WARNING: $OTS_TRUSTSTORE not found — truststore not staged for download" >&2
fi

cat <<EOF

OpenTAKServer fixup complete.
  Nucleus web UI : http(s)://<node-ip>/            (80/443, unchanged)
  OTS web UI     : https://<node-ip>:${OTS_HTTPS_PORT}/
  OTS HTTP       : http://<node-ip>:${OTS_HTTP_PORT}/  (plain HTTP UI + Marti API)
  OTS unchanged  : 8443 (Marti, client cert), 8446 (enrollment), 8089 (TLS clients)
Re-run this script after every OpenTAKServer install or upgrade.
EOF
