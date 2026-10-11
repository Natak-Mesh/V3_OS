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
    python3 python3-venv python3-pip git \
    babeld smcroute hostapd wpasupplicant iw \
    nftables network-manager ufw \
    avahi-daemon avahi-utils \
    ca-certificates wget

echo "==> free the GPIO UART for the flight controller"
# The flight controller is wired to the Pi GPIO UART (/dev/serial0 -> ttyS0).
# Stock Pi OS puts a kernel console and a login getty on that port, which write
# boot/login text to the FC and swallow its data. Enable the UART, drop the
# serial console from the kernel cmdline, and mask the getty so nothing but the
# FC link ever owns the port. The cmdline change takes effect on next reboot.
BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if [ -f "$BOOTCFG" ] && ! grep -q '^enable_uart=1' "$BOOTCFG"; then
    echo 'enable_uart=1' >> "$BOOTCFG"
    echo "    enabled UART in $BOOTCFG (reboot required)"
fi
CMDLINE=/boot/firmware/cmdline.txt
[ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
if [ -f "$CMDLINE" ] && grep -qE 'console=(serial0|ttyS0|ttyAMA0)' "$CMDLINE"; then
    sed -i -E 's/console=(serial0|ttyS0|ttyAMA0),[0-9]+ //g' "$CMDLINE"
    echo "    removed serial console from $CMDLINE (reboot required)"
fi
for tty in ttyS0 ttyAMA0; do
    systemctl disable --now "serial-getty@$tty.service" 2>/dev/null || true
    systemctl mask "serial-getty@$tty.service" 2>/dev/null || true
done

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
mkdir -p /etc/NetworkManager/conf.d
install -m 644 "$REPO/system/networkmanager/unmanaged-devices.conf" \
    /etc/NetworkManager/conf.d/unmanaged-devices.conf

echo "==> node update tooling"
# Update script, run over ssh: pull + reinstall + apply.
install -m 755 "$REPO/system/bin/nucleus-update.sh" "$OPT/bin/nucleus-update.sh"

echo "==> mavlink-router (flight controller UART <-> UDP 14550)"
# Official prebuilt aarch64 binary; not in the Debian repos. Downloaded only when
# the pinned version changes (marker file), so re-runs don't hit the network.
MAVLINK_ROUTER_VERSION=v4
MAVLINK_ROUTER_URL="https://github.com/mavlink-router/mavlink-router/releases/download/$MAVLINK_ROUTER_VERSION/mavlink-routerd-glibc-aarch64"
MAVLINK_ROUTER_MARKER="$OPT/mavlink-routerd.version"
if [ ! -x /usr/local/bin/mavlink-routerd ] || \
   [ "$(cat "$MAVLINK_ROUTER_MARKER" 2>/dev/null)" != "$MAVLINK_ROUTER_VERSION" ]; then
    wget -q -O /usr/local/bin/mavlink-routerd.tmp "$MAVLINK_ROUTER_URL"
    chmod 755 /usr/local/bin/mavlink-routerd.tmp
    mv /usr/local/bin/mavlink-routerd.tmp /usr/local/bin/mavlink-routerd
    echo "$MAVLINK_ROUTER_VERSION" > "$MAVLINK_ROUTER_MARKER"
    echo "    installed mavlink-routerd $MAVLINK_ROUTER_VERSION"
else
    echo "    mavlink-routerd $MAVLINK_ROUTER_VERSION already installed"
fi
mkdir -p /etc/mavlink-router
install -m 644 "$REPO/system/mavlink-router/main.conf" /etc/mavlink-router/main.conf
install -m 644 "$REPO/system/systemd/mavlink-router.service" /etc/systemd/system/
# USB GPS -> FC bridge (GPS_INPUT into mavlink-router's local 14560 endpoint).
install -m 644 "$REPO/system/systemd/nucleus-gps.service" /etc/systemd/system/

echo "==> seed config (only if missing)"
mkdir -p /etc/nucleus
if [ ! -f /etc/nucleus/config.yaml ]; then
    install -m 644 "$REPO/config/config.yaml" /etc/nucleus/config.yaml
    echo "    seeded /etc/nucleus/config.yaml — EDIT node.id before applying"
else
    echo "    /etc/nucleus/config.yaml exists — left untouched"
fi

echo "==> remove subsystems dropped from this build"
# Nodes upgraded from a full Nucleus build still carry units, rules and launchers
# for subsystems this build no longer ships (Meshtastic/CoT bridge, messaging,
# voice, Reticulum, TAK, Tailscale, the web UI/HTTP layer). Their code is gone from the venv, so stop
# and remove them. Idempotent: every step is a no-op on a fresh node.
for u in cot-bridge nucleus-meshtastic-init nucleus-messaging nucleus-voice rnsd meshtasticd tailscaled nucleusd; do
    systemctl disable --now "$u.service" 2>/dev/null || true
done
rm -f /etc/systemd/system/cot-bridge.service \
      /etc/systemd/system/nucleus-meshtastic-init.service \
      /etc/systemd/system/nucleus-messaging.service \
      /etc/systemd/system/nucleus-voice.service \
      /etc/systemd/system/rnsd.service \
      /etc/systemd/system/nucleusd.service
rm -rf /etc/systemd/system/takserver.service.d /etc/nucleus/certs
rm -f /etc/sudoers.d/nucleus-meshtastic /etc/sudoers.d/nucleus-update \
      /etc/nginx/sites-enabled/nucleus /etc/nginx/sites-available/nucleus \
      /etc/nginx/nucleus.htpasswd \
      /etc/udev/rules.d/60-nucleus-usb.rules /etc/udev/rules.d/60-meshtastic.rules \
      /etc/apt/sources.list.d/meshtastic.list /etc/apt/sources.list.d/tailscale.list
rm -f "$OPT/bin/nucleus-tak-setup.sh" "$OPT/bin/nucleus-ots-fixup.sh" \
      /usr/local/bin/nucleus-tak-setup.sh /usr/local/bin/nucleus-ots-fixup.sh
for t in rnstatus rnpath rnprobe rnid rncp rnx rnsd; do
    rm -f "/usr/local/bin/$t"
done

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
# avahi advertises <serial>-nucleus.local over mDNS (ssh by name).
systemctl enable avahi-daemon
systemctl enable systemd-networkd nucleus-mesh.service babeld smcroute hostapd brlan-setup.service
# mavlink-router is static (not rendered by apply); restart so a re-install
# picks up a new binary or main.conf.
systemctl enable mavlink-router.service
systemctl restart mavlink-router.service
systemctl enable nucleus-gps.service
systemctl restart nucleus-gps.service

echo
echo "Install complete. Next:"
echo "  1. edit /etc/nucleus/config.yaml  (set node.id etc.)"
echo "  2. sudo nucleusctl apply           (render configs + start units)"
