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
    python3 python3-venv python3-pip git curl gpg \
    babeld smcroute hostapd wpasupplicant iw \
    nftables network-manager \
    nginx avahi-daemon avahi-utils openssl

echo "==> meshtasticd (native, from the Meshtastic apt repo)"
# Native meshtasticd replaces the V2 Docker container. The repo ships the
# per-slot LoRa configs under /etc/meshtasticd/available.d, but we render the
# full /etc/meshtasticd/config.yaml ourselves (nucleusctl apply), so overlay
# merging is not relied upon.
MESHREPO=/etc/apt/sources.list.d/meshtastic.list
if [ ! -f "$MESHREPO" ]; then
    . /etc/os-release
    # Pick the OBS suite from the running OS — never hardcode. OBS publishes
    # Debian_13 / Raspbian_13 (trixie) etc; a wrong suite pulls a build with
    # unsatisfiable libs (e.g. bookworm's libgpiod2 on trixie).
    DISTRO="Debian"; [ "$ID" = "raspbian" ] && DISTRO="Raspbian"
    SUITE="${DISTRO}_${VERSION_ID}"
    KEYURL="https://download.opensuse.org/repositories/network:/Meshtastic:/beta/${SUITE}/Release.key"
    REPOBASE="https://download.opensuse.org/repositories/network:/Meshtastic:/beta/${SUITE}/"
    mkdir -p /etc/apt/keyrings
    # Remove any key from a previously-failed run so gpg never prompts to
    # overwrite (--yes) and never dearmors a stale/empty file.
    rm -f /etc/apt/keyrings/meshtastic.gpg
    curl -fsSL "$KEYURL" | gpg --dearmor --yes -o /etc/apt/keyrings/meshtastic.gpg
    echo "deb [signed-by=/etc/apt/keyrings/meshtastic.gpg] ${REPOBASE} /" > "$MESHREPO"
fi
apt-get update -qq
apt-get install -y meshtasticd || echo "WARNING: meshtasticd install failed — check the Meshtastic apt repo"
# We manage meshtasticd via nucleusctl apply (enable/disable per config), so
# disable the packaged unit's default autostart here; apply reconciles it.
systemctl disable meshtasticd 2>/dev/null || true

echo "==> GPS UART prep"
# The RAK hat GPS is a UART GPS on the Pi GPIO header (/dev/ttyS0). Ensure the
# UART is enabled and the serial console is NOT holding the port.
BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if [ -f "$BOOTCFG" ] && ! grep -q '^enable_uart=1' "$BOOTCFG"; then
    echo 'enable_uart=1' >> "$BOOTCFG"
    echo "    enabled UART in $BOOTCFG (reboot required)"
fi
CMDLINE=/boot/firmware/cmdline.txt
[ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
if [ -f "$CMDLINE" ] && grep -q 'console=serial0' "$CMDLINE"; then
    sed -i 's/console=serial0,[0-9]* //g' "$CMDLINE"
    echo "    removed serial console from $CMDLINE (frees /dev/ttyS0 for GPS)"
fi

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
install -m 644 "$REPO/system/systemd/brlan-setup.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/nucleusd.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/cot-bridge.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/nucleus-meshtastic-init.service" /etc/systemd/system/
install -m 644 "$REPO/system/udev/60-meshtastic.rules" /etc/udev/rules.d/
mkdir -p /etc/NetworkManager/conf.d
install -m 644 "$REPO/system/networkmanager/unmanaged-devices.conf" \
    /etc/NetworkManager/conf.d/unmanaged-devices.conf

# The web UI (running as user 'natak') pauses/restarts cot-bridge during radio
# config ops. Allow only those specific systemctl actions without a password.
cat > /etc/sudoers.d/nucleus-meshtastic <<'EOF'
natak ALL=(root) NOPASSWD: /usr/bin/systemctl stop cot-bridge.service, /usr/bin/systemctl start cot-bridge.service, /usr/bin/systemctl restart cot-bridge.service, /usr/bin/systemctl is-active cot-bridge.service
EOF
chmod 440 /etc/sudoers.d/nucleus-meshtastic

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
# Debian ships hostapd masked by default; unmask before enabling (idempotent).
# Global wpa_supplicant conflicts with hostapd (wedges brcmfmac firmware so the
# AP beacons but silently drops all auth). v2 disabled it; keep it dead.
systemctl disable --now wpa_supplicant.service 2>/dev/null || true
systemctl mask wpa_supplicant.service 2>/dev/null || true
systemctl unmask hostapd
# hostapd's ExecStart uses ${DAEMON_CONF}; point it at our rendered config.
if ! grep -q '^DAEMON_CONF="/etc/hostapd/hostapd.conf"' /etc/default/hostapd 2>/dev/null; then
    sed -i '/^DAEMON_CONF=/d' /etc/default/hostapd 2>/dev/null || true
    echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >> /etc/default/hostapd
fi
# avahi advertises <serial>-nucleus.local over mDNS; nginx reverse-proxies
# :80/:443 -> the uvicorn web UI on :8080 (rendered by `nucleusctl apply`).
systemctl enable avahi-daemon nginx
systemctl enable systemd-networkd nucleus-mesh.service babeld smcroute hostapd brlan-setup.service nucleusd.service

echo
echo "Install complete. Next:"
echo "  1. edit /etc/nucleus/config.yaml  (set node.id etc.)"
echo "  2. sudo nucleusctl apply           (render configs + start units)"
echo "  3. browse http://<serial>-nucleus.local  (web UI, no port; via nginx+avahi)"
echo "     or http://<node-ip>:8080             (direct fallback)"
