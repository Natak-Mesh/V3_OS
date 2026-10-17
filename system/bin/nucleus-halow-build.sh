#!/bin/bash
# ============================================================
# Nucleus V3 OS — Morse MM8108 Wi-Fi HaLow driver/firmware/supplicant builder.
#
# Productionised form of ~natak/halow/halow-setup.sh (docs/halow.md §10.6):
#   * driver built + installed via DKMS so kernel updates auto-rebuild it,
#   * firmware staged into /lib/firmware/morse (no global sysfs path override),
#   * wpa_supplicant_s1g (+ wpa_cli_s1g, morse_cli) installed to /opt/nucleus/bin,
#   * a stamp file records the pinned tags + kernel so install.sh can skip a
#     rebuild when nothing changed (fast no-op on every subsequent run).
#
# Idempotent. Called by install.sh only when the stamp is absent or stale; also
# runnable by hand. Everything heavy lives under /opt/nucleus/halow.
# ============================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root" >&2
    exit 1
fi

# Pinned upstream tags (docs/halow.md §2). Keep in lock-step with the stamp.
DRIVER_TAG="mm8108-2.1.0"
FIRMWARE_TAG="mm8108-2.0.0"
HOSTAP_TAG="mm8108-2.1.0"
MORSE_CLI_TAG="mm8108-2.1.0"

HALOW_DIR="${HALOW_DIR:-/opt/nucleus/halow}"
SRC="$HALOW_DIR/src"
BIN="/opt/nucleus/bin"
FW_DEST="/lib/firmware/morse"
DKMS_NAME="morse"
DKMS_VER="$DRIVER_TAG"
DKMS_SRC="/usr/src/${DKMS_NAME}-${DKMS_VER}"
STAMP="$HALOW_DIR/.built"
KVER="$(uname -r)"
KBUILD="/lib/modules/$KVER/build"
J="$(nproc)"

# --- fast path: stamp matches -> nothing to do -------------------------------
WANT_STAMP="driver=$DRIVER_TAG firmware=$FIRMWARE_TAG hostap=$HOSTAP_TAG cli=$MORSE_CLI_TAG kernel=$KVER"
if [ "${1:-}" != "--force" ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$WANT_STAMP" ]; then
    echo "nucleus-halow-build: up to date ($WANT_STAMP)"
    exit 0
fi

echo "== deps"
# libnl-route-3-dev: the hostap fork's driver_nl80211 links -lnl-route-3.
# dkms: kernel-update-safe driver rebuilds.
PKGS="git build-essential pkg-config python3-yaml dkms \
libnl-3-dev libnl-genl-3-dev libnl-route-3-dev libssl-dev libusb-1.0-0-dev"
[ -d "$KBUILD" ] || PKGS="$PKGS linux-headers-$KVER"
apt-get update -qq
apt-get install -y --no-install-recommends $PKGS

mkdir -p "$SRC" "$BIN" "$FW_DEST"

clone() {  # url dir tag
    if [ -d "$2/.git" ]; then
        git -C "$2" fetch -q --depth 1 origin "$3" || true
        git -C "$2" checkout -q "$3" || true
    else
        git clone -q --depth 1 --branch "$3" --recurse-submodules "$1" "$2"
    fi
}
echo "== clone (pinned tags)"
clone https://github.com/MorseMicro/morse_driver.git   "$SRC/morse_driver"   "$DRIVER_TAG"
clone https://github.com/MorseMicro/morse-firmware.git "$SRC/morse-firmware" "$FIRMWARE_TAG"
clone https://github.com/MorseMicro/hostap.git         "$SRC/hostap"         "$HOSTAP_TAG"
clone https://github.com/MorseMicro/morse_cli.git      "$SRC/morse_cli"      "$MORSE_CLI_TAG"

# --- driver via DKMS ---------------------------------------------------------
# DKMS builds both morse.ko and dot11ah.ko with the exact config flags the driver
# needs (docs/halow.md §2 "Non-obvious build flags") and reinstalls them on every
# kernel bump via the kernel package's dkms hook. Modules land in
# /lib/modules/<kver>/updates/dkms and autoload on plug via modalias.
echo "== driver (DKMS)"
MAKE_FLAGS="KERNEL_SRC=\$kernel_source_dir \
CONFIG_WLAN_VENDOR_MORSE=m CONFIG_MORSE_USB=y CONFIG_MORSE_USER_ACCESS=y \
CONFIG_MORSE_VENDOR_COMMAND=y CONFIG_MORSE_COUNTRY='\"US\"'"

rm -rf "$DKMS_SRC"
cp -a "$SRC/morse_driver" "$DKMS_SRC"
cat > "$DKMS_SRC/dkms.conf" <<EOF
PACKAGE_NAME="$DKMS_NAME"
PACKAGE_VERSION="$DKMS_VER"
MAKE[0]="make -j$J $MAKE_FLAGS"
CLEAN="make clean"
BUILT_MODULE_NAME[0]="morse"
DEST_MODULE_LOCATION[0]="/updates/dkms"
BUILT_MODULE_NAME[1]="dot11ah"
BUILT_MODULE_LOCATION[1]="dot11ah"
DEST_MODULE_LOCATION[1]="/updates/dkms"
AUTOINSTALL="yes"
EOF

# Remove any prior registration of this version, then add/build/install.
dkms remove -m "$DKMS_NAME" -v "$DKMS_VER" --all 2>/dev/null || true
dkms add -m "$DKMS_NAME" -v "$DKMS_VER"
dkms build -m "$DKMS_NAME" -v "$DKMS_VER"
dkms install -m "$DKMS_NAME" -v "$DKMS_VER" --force
depmod -a "$KVER"

# --- firmware ----------------------------------------------------------------
# Into /lib/firmware/morse so the kernel firmware loader finds it with NO global
# sysfs path override (kills the §9 open item). Copy (not symlink) so it survives
# the src tree being cleaned.
echo "== firmware -> $FW_DEST"
find "$SRC/morse-firmware/firmware" -name 'mm8108*.bin' -exec cp -f {} "$FW_DEST/" \;
find "$SRC/morse-firmware/bcf"      -name 'bcf_*.bin'   -exec cp -f {} "$FW_DEST/" \;

# --- Morse wpa_supplicant ----------------------------------------------------
echo "== wpa_supplicant_s1g -> $BIN"
HS="$SRC/hostap/wpa_supplicant"
if [ ! -x "$HS/wpa_supplicant_s1g" ]; then
    cp "$HS/defconfig" "$HS/.config"
    sed -i 's/^#CONFIG_MESH=y/CONFIG_MESH=y/; s/^CONFIG_CTRL_IFACE_DBUS/#&/' "$HS/.config"
    # Fork's DPP code uses OpenSSL 3.0-deprecated EC_KEY calls; -Werror would stop it.
    grep -q 'Wno-deprecated-declarations' "$HS/.config" || \
        echo 'CFLAGS += -Wno-deprecated-declarations' >> "$HS/.config"
    make -C "$HS" -j"$J" wpa_supplicant_s1g wpa_cli_s1g
fi
install -m 0755 "$HS/wpa_supplicant_s1g" "$BIN/wpa_supplicant_s1g"
install -m 0755 "$HS/wpa_cli_s1g" "$BIN/wpa_cli_s1g"

echo "== morse_cli (optional) -> $BIN"
if [ ! -x "$SRC/morse_cli/morse_cli" ]; then
    make -C "$SRC/morse_cli" -j"$J" CONFIG_MORSE_TRANS_NL80211=1 \
        || echo "WARN: morse_cli build failed (not needed for mesh)"
fi
[ -x "$SRC/morse_cli/morse_cli" ] && install -m 0755 "$SRC/morse_cli/morse_cli" "$BIN/morse_cli"

echo "$WANT_STAMP" > "$STAMP"
echo "== done: $WANT_STAMP"
