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
    nftables network-manager ufw \
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

echo "==> Reticulum CLI launchers"
# rns ships its tools in the venv; symlink them onto PATH so operators can run
# `rnstatus` etc. directly instead of the full venv path.
for t in rnstatus rnpath rnprobe rnid rncp rnx rnsd; do
    ln -sf "$VENV/bin/$t" "/usr/local/bin/$t"
done

echo "==> static system files"
install -m 644 "$REPO/system/systemd/nucleus-mesh.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/brlan-setup.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/nucleusd.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/cot-bridge.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/nucleus-meshtastic-init.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/nucleus-messaging.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/nucleus-voice.service" /etc/systemd/system/
install -m 644 "$REPO/system/systemd/rnsd.service" /etc/systemd/system/
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

echo "==> node update tooling"
# Update script the web UI (UPDATE page) launches to pull + reinstall + restart.
install -m 755 "$REPO/system/bin/nucleus-update.sh" "$OPT/bin/nucleus-update.sh"
# Let the web UI user launch it detached, as root, without a password.
install -m 440 "$REPO/system/sudoers.d/nucleus-update" /etc/sudoers.d/nucleus-update

echo "==> TAK Server provisioning tooling (inert unless tak.variant=official)"
# One-shot official TAK Server provisioner. NOT wired into apply — it's a manual
# per-node action (see README §4). Copied to every node so it's ready when
# needed; it no-ops unless config.yaml sets tak.variant=official.
install -m 755 "$REPO/system/bin/nucleus-tak-setup.sh" "$OPT/bin/nucleus-tak-setup.sh"
ln -sf "$OPT/bin/nucleus-tak-setup.sh" /usr/local/bin/nucleus-tak-setup.sh
# Boot-ordering drop-in for takserver.service. Inert on nodes without takserver
# installed — systemd only applies a drop-in whose base unit exists.
mkdir -p /etc/systemd/system/takserver.service.d
install -m 644 "$REPO/system/systemd/takserver.service.d/override.conf" \
    /etc/systemd/system/takserver.service.d/override.conf

echo "==> seed config (only if missing)"
mkdir -p /etc/nucleus
if [ ! -f /etc/nucleus/config.yaml ]; then
    install -m 644 "$REPO/config/config.yaml" /etc/nucleus/config.yaml
    echo "    seeded /etc/nucleus/config.yaml — EDIT node.id before applying"
else
    echo "    /etc/nucleus/config.yaml exists — left untouched"
fi

echo "==> Reticulum config is rendered by 'nucleusctl apply'"
# rnsd runs as user 'natak' and reads ~natak/.reticulum/config. That file is a
# rendered artifact now (templates/reticulum-config.j2), produced from the one
# config.yaml like every other generated file — no seeding here.

echo "==> Tailscale (installed from Tailscale's apt repo, left logged-out)"
# Tailscale ships its own apt repo (pkgs.tailscale.com), one suite per Debian
# codename. We add it directly here — same pattern as the meshtastic repo above —
# rather than piping their curl|sh installer, which spawns its OWN apt-get and
# collides with this script over the dpkg lock. All package installs stay in one
# apt sequence. Run 'tailscale up' (or use the web UI) to activate.
TSREPO=/etc/apt/sources.list.d/tailscale.list
if [ ! -f "$TSREPO" ]; then
    . /etc/os-release
    CODENAME="${VERSION_CODENAME:-trixie}"
    mkdir -p /usr/share/keyrings
    curl -fsSL "https://pkgs.tailscale.com/stable/debian/${CODENAME}.noarmor.gpg" \
        -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL "https://pkgs.tailscale.com/stable/debian/${CODENAME}.tailscale-keyring.list" \
        -o "$TSREPO"
fi
apt-get update -qq
apt-get install -y -o DPkg::Lock::Timeout=120 tailscale
systemctl enable tailscaled

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
systemctl enable systemd-networkd nucleus-mesh.service babeld smcroute hostapd brlan-setup.service nucleusd.service nucleus-messaging.service nucleus-voice.service rnsd.service

# Restart the always-on app daemons so a code-only re-install (new package in the
# venv) actually takes effect — enabling alone won't reload a running process.
# These own the API/web UI, messaging and voice; the mesh/network units are
# reconciled by `nucleusctl apply`, so they're deliberately left to that path.
echo "==> restart app daemons (pick up new code)"
systemctl restart nucleusd.service nucleus-messaging.service nucleus-voice.service

echo
echo "Install complete. Next:"
echo "  1. edit /etc/nucleus/config.yaml  (set node.id etc.)"
echo "  2. sudo nucleusctl apply           (render configs + start units)"
echo "  3. browse http://<serial>-nucleus.local  (web UI, no port; via nginx+avahi)"
echo "     or http://<node-ip>:8080             (direct fallback)"
