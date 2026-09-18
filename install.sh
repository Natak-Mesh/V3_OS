#!/bin/bash
# ============================================================
# Nucleus V3 OS — idempotent installer.
#
# Repo is the source of truth (Option A). This script syncs the repo into the
# live system: installs packages, builds the venv, copies static system units,
# installs the nucleusd package, seeds /etc/nucleus/config.yaml (only if absent),
# and enables services. Safe to re-run.
#
# Usage:  sudo ./install.sh
# ============================================================
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
OPT=/opt/nucleus
VENV="$OPT/venv"

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root (sudo ./install.sh)" >&2
    exit 1
fi

echo "==> apt packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    babeld smcroute hostapd wpasupplicant iw \
    nftables network-manager

echo "==> python venv at $VENV"
mkdir -p "$OPT/bin"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "$REPO"

echo "==> nucleusctl launcher"
cat > /usr/local/bin/nucleusctl <<EOF
#!/bin/bash
exec "$VENV/bin/python" -m nucleusd.cli "\$@"
EOF
chmod +x /usr/local/bin/nucleusctl

echo "==> static system files"
install -m 644 "$REPO/system/systemd/nucleus-mesh.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/nucleusd.service" /etc/systemd/system/
mkdir -p /etc/NetworkManager/conf.d
install -m 644 "$REPO/system/networkmanager/unmanaged-devices.conf" \
    /etc/NetworkManager/conf.d/unmanaged-devices.conf

echo "==> seed config (only if missing)"
mkdir -p /etc/nucleus
if [ ! -f /etc/nucleus/config.yaml ]; then
    install -m 644 "$REPO/config/config.yaml" /etc/nucleus/config.yaml
    echo "    seeded /etc/nucleus/config.yaml — EDIT node.id before applying"
else
    echo "    /etc/nucleus/config.yaml exists — left untouched"
fi

echo "==> enable services"
systemctl daemon-reload
systemctl enable systemd-networkd nucleus-mesh.service babeld smcroute hostapd nucleusd.service

echo
echo "Install complete. Next:"
echo "  1. edit /etc/nucleus/config.yaml  (set node.id etc.)"
echo "  2. sudo nucleusctl apply           (render configs + start units)"
echo "  3. browse http://<node-ip>:8080    (web UI)"
