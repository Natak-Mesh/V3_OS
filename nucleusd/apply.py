"""Render -> diff -> restart engine.

This is the heart of the "config.yaml is the only thing you edit" model. It
renders every Jinja2 template against the validated config, writes only the
files whose content actually changed, and restarts only the systemd units whose
inputs changed. Idempotent: re-running with no config change touches nothing.

Importable as a plain module (used by both the CLI and the REST API), so the
apply logic exists in exactly one place.
"""

from __future__ import annotations

import pwd
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Reticulum daemon (rnsd) runs as this user; its config lives in ~/.reticulum.
RETI_USER = "natak"
RETI_CONFIG = Path(f"/home/{RETI_USER}/.reticulum/config")

# nginx reverse-proxy paths (port-less .local + by-IP access to the :8080 UI).
NGINX_VHOST = Path("/etc/nginx/sites-available/nucleus")
NGINX_ENABLED = Path("/etc/nginx/sites-enabled/nucleus")
NGINX_DEFAULT = Path("/etc/nginx/sites-enabled/default")
NGINX_HTPASSWD = Path("/etc/nginx/nucleus.htpasswd")
# Generated udev rule binding recognised USB devices to /dev/nucleus/<role>.
USB_UDEV_RULE = Path("/etc/udev/rules.d/60-nucleus-usb.rules")
# HaLow (802.11ah) generated artifacts.
HALOW_SUPPLICANT_CONF = Path("/etc/wpa_supplicant/wpa_supplicant-halow.conf")
HALOW_UP_SCRIPT = Path("/opt/nucleus/bin/nucleus-halow-up.sh")
HALOW_MODPROBE_CONF = Path("/etc/modprobe.d/nucleus-morse.conf")
CERT_DIR = Path("/etc/nucleus/certs")
CERT_CRT = CERT_DIR / "nucleus-web.crt"
CERT_KEY = CERT_DIR / "nucleus-web.key"

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import rf
from .schema import NucleusConfig

# Meshtastic daemon exposes its API on localhost; the CLI drives it the same way
# radio_init does. Module-level so tests can monkeypatch subprocess.run.
import sys as _sys
MESHTASTIC_CLI = [_sys.executable, "-m", "meshtastic", "--host", "localhost"]

# Mesh units whose lifecycle is owned by mesh.enabled (RF reconcile), not by a
# plain restart. nucleus-mesh brings wlan1 up; babeld/smcroute route over it.
MESH_UNITS = ["nucleus-mesh", "babeld", "smcroute"]

# HaLow service, enable/disable-driven by halow.enabled (like meshtasticd/rnsd).
# It's in NET_GROUP_ORDER so the group bounce restarts it when its inputs change,
# but it must be dropped from that bounce when HaLow is disabled (same pattern as
# MESH_UNITS) so a disabled HaLow never comes back up.
HALOW_UNIT = "nucleus-halow"

TEMPLATE_DIR = Path(__file__).parent / "templates"

# Each target maps a rendered template to its destination and the units that
# must restart when it changes. Order matters: networkd first, then mesh join,
# then the routing daemons that depend on the interfaces existing.
@dataclass(frozen=True)
class Target:
    template: str
    dest: Path
    units: tuple[str, ...] = ()
    mode: int = 0o644


# brlan-setup (static unit, system/systemd/brlan-setup.service) enslaves wlan0
# into br-lan after hostapd has put it in AP mode. Reloading networkd or
# restarting hostapd drops wlan0 out of br-lan (no AP DHCP), so every target
# that bounces either of those must also re-run brlan-setup.
TARGETS: list[Target] = [
    Target("networkd/20-br-lan.netdev.j2", Path("/etc/systemd/network/20-br-lan.netdev"), ("systemd-networkd", "brlan-setup")),
    Target("networkd/21-br-lan.network.j2", Path("/etc/systemd/network/21-br-lan.network"), ("systemd-networkd", "brlan-setup")),
    Target("networkd/30-wlan0.network.j2", Path("/etc/systemd/network/30-wlan0.network"), ("systemd-networkd", "brlan-setup")),
    Target("networkd/40-eth0.network.j2", Path("/etc/systemd/network/40-eth0.network"), ("systemd-networkd", "brlan-setup")),
    Target("networkd/10-wlan1.network.j2", Path("/etc/systemd/network/10-wlan1.network"), ("systemd-networkd", "brlan-setup")),
    Target("networkd/12-halow0.network.j2", Path("/etc/systemd/network/12-halow0.network"), ("systemd-networkd", "brlan-setup", "nucleus-halow")),
    Target("wpa_supplicant-mesh.conf.j2", Path("/etc/wpa_supplicant/wpa_supplicant-mesh.conf"), ("nucleus-mesh",), 0o600),
    Target("wpa_supplicant-halow.conf.j2", HALOW_SUPPLICANT_CONF, ("nucleus-halow",), 0o600),
    Target("nucleus-mesh-up.sh.j2", Path("/opt/nucleus/bin/nucleus-mesh-up.sh"), ("nucleus-mesh",), 0o755),
    Target("nucleus-halow-up.sh.j2", HALOW_UP_SCRIPT, ("nucleus-halow",), 0o755),
    # modprobe.d conf for the Morse driver country — no unit (modprobe, not systemd).
    Target("nucleus-morse.conf.j2", HALOW_MODPROBE_CONF, ()),
    Target("babeld.conf.j2", Path("/etc/babeld.conf"), ("babeld",)),
    Target("smcroute.conf.j2", Path("/etc/smcroute.conf"), ("smcroute",)),
    Target("hostapd.conf.j2", Path("/etc/hostapd/hostapd.conf"), ("hostapd", "brlan-setup")),
    Target("meshtasticd-config.yaml.j2", Path("/etc/meshtasticd/config.yaml"), ("meshtasticd",)),
    # rnsd is enable/disable-driven like meshtasticd (handled in apply()), and
    # the file must land owned by RETI_USER — see _write_reticulum below.
    Target("reticulum-config.j2", RETI_CONFIG, ("rnsd",)),
    # USB device → /dev/nucleus/<role> udev rule. udev is not a systemd unit, so
    # a change triggers a udevadm reload/trigger specially in apply() (no units).
    Target("60-nucleus-usb.rules.j2", USB_UDEV_RULE, ()),
    # nginx reverse proxy — reload (not restart) handled specially in apply().
    Target("nginx-nucleus.conf.j2", NGINX_VHOST, ()),
    # HTTP Basic credentials for web UI over eth0 — reload nginx on change.
    Target("nucleus.htpasswd.j2", NGINX_HTPASSWD, ()),
]


@dataclass
class ApplyResult:
    changed: list[str] = field(default_factory=list)
    units_restarted: list[str] = field(default_factory=list)
    dry_run: bool = False


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,  # fail loudly on a missing variable
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_all(cfg: NucleusConfig) -> dict[str, str]:
    """Render every template to a {dest_path: content} map. Pure, no writes.

    Used directly by unit tests to assert output off-box.
    """
    env = _env()
    ctx = cfg.render_context()
    return {str(t.dest): env.get_template(t.template).render(**ctx) for t in TARGETS}


def _write_if_changed(dest: Path, content: str, mode: int) -> bool:
    """Write only if content differs. Returns True if the file changed."""
    if dest.exists() and dest.read_text() == content:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    dest.chmod(mode)
    return True


# Links reconfigured (not just reloaded) when the network group is rebuilt.
# `networkctl reload` only re-reads files; existing links need `reconfigure` to
# actually re-apply their config.
NETWORKD_LINKS = ("br-lan", "wlan1", "halow0", "eth0", "wlan0")

# The network group: if ANY of these targets change we restart the whole group
# in a fixed order, because bouncing one (wlan1/br-lan/hostapd) leaves the others
# running against stale interfaces and multicast memberships. nucleus-halow runs
# after nucleus-mesh (the HaLow join is independent, but ordered after the primary
# mesh) and before the routing daemons that bind to halow0.
NET_GROUP_ORDER = ["systemd-networkd", "nucleus-mesh", "nucleus-halow", "hostapd", "brlan-setup", "babeld", "smcroute"]


def _restart(units: list[str]) -> None:
    if not units:
        return
    # networkd is reconfigured per-link, everything else restarted.
    for unit in units:
        if unit == "systemd-networkd":
            # reload picks up new/removed .network files; reconfigure re-applies
            # config to existing links (reconfigure alone won't load a new file).
            subprocess.run(["networkctl", "reload"], check=False)
            subprocess.run(["networkctl", "reconfigure", *NETWORKD_LINKS], check=False)
        else:
            subprocess.run(["systemctl", "restart", f"{unit}.service"], check=False)


def _set_service(unit: str, want_on: bool) -> None:
    """Enable+start or stop+disable a unit to match desired state. Idempotent."""
    if want_on:
        subprocess.run(["systemctl", "enable", "--now", f"{unit}.service"], check=False)
    else:
        subprocess.run(["systemctl", "disable", "--now", f"{unit}.service"], check=False)


def _reconcile_meshtastic(cfg: NucleusConfig) -> list[str]:
    """Enable/disable meshtasticd + cot-bridge to match the config flags.

    Runs after templates are written so the units start against fresh config.
    The cot-bridge only runs when meshtasticd is enabled AND cot_bridge is on.
    Returns the list of units whose enabled-state was reconciled.
    """
    m = cfg.meshtastic
    touched: list[str] = []
    _set_service("meshtasticd", m.enabled)
    touched.append("meshtasticd")
    _set_service("nucleus-meshtastic-init", m.enabled)
    touched.append("nucleus-meshtastic-init")
    _set_service("cot-bridge", m.enabled and m.cot_bridge)
    touched.append("cot-bridge")
    return touched


def _reconcile_rf(cfg: NucleusConfig) -> list[str]:
    """Reconcile the wlan1 mesh RF + units to match mesh.enabled. Idempotent.

    Two coupled actions, matching nucleus-rf.service's boot behaviour:
      * rfkill phy1 (via rf.py) to block/unblock the mesh radio, and
      * enable/disable the mesh units (nucleus-mesh, babeld, smcroute).

    Turning the mesh back ON is NOT just an unblock. rfkill-down drops wlan1's
    IPv6 link-local and leaves wpa_supplicant disconnected, so the radio comes
    back but the mesh does not: babeld ignores a link-local-less interface and no
    peer can see this node. `enable --now` is a no-op when the units are already
    running, so coming out of a block we must RESTART the mesh units to redo the
    SAE join (nucleus-mesh) and rebind babeld/smcroute. We only do that on the
    actual blocked->unblocked transition, keeping a steady-state apply idempotent.

    Only the wlan1 mesh is config-driven; the AP (wlan0) is outside the pipeline
    (README §4) and is not touched here. Returns the units whose state was set.
    """
    enabled = cfg.mesh.enabled
    was_blocked = rf.is_blocked(rf.phy_for_iface(rf.MESH_IFACE) or "")

    if enabled:
        # Unblock the radio first, then (re)start the mesh units. On a real
        # off->on transition restart them so the join + link-local come back;
        # otherwise just ensure they are enabled and running (idempotent).
        rf.set_iface_blocked(rf.MESH_IFACE, blocked=False)
        for unit in MESH_UNITS:
            _set_service(unit, True)
        if was_blocked:
            _restart([u for u in NET_GROUP_ORDER if u in MESH_UNITS])
    else:
        # Stop routing before the radio goes away, then block the phy.
        for unit in MESH_UNITS:
            _set_service(unit, False)
        rf.set_iface_blocked(rf.MESH_IFACE, blocked=True)
    return list(MESH_UNITS)


def _reconcile_halow(cfg: NucleusConfig, changed: list[str]) -> list[str]:
    """Enable/disable nucleus-halow to match halow.enabled; restart on conf change.

    Mirrors _reconcile_meshtastic: the service is bound to the halow0 device and
    only actually runs while the radio is present, so enabling it on a radio-less
    node is inert. If the supplicant conf or the up-script changed AND HaLow is
    enabled, restart nucleus-halow so a live channel/bandwidth edit rejoins the
    mesh without a reboot or module reload. Returns the units touched.
    """
    _set_service(HALOW_UNIT, cfg.halow.enabled)
    if cfg.halow.enabled and (
        str(HALOW_SUPPLICANT_CONF) in changed or str(HALOW_UP_SCRIPT) in changed
    ):
        _restart([HALOW_UNIT])
    return [HALOW_UNIT]


def _meshtastic_cli(args: list[str], timeout: int = 120) -> tuple[int, str]:
    """Run the meshtastic CLI against the local daemon; never raise."""
    try:
        r = subprocess.run(MESHTASTIC_CLI + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001 — best-effort radio control
        return -1, str(e)


def _meshtastic_get_bool(key: str) -> bool | None:
    """Read a boolean firmware setting via `meshtastic --get <key>`.

    Returns True/False, or None if the value can't be read/parsed (radio absent,
    daemon down). Output line looks like 'lora.tx_enabled: True'.
    """
    rc, out = _meshtastic_cli(["--get", key], timeout=30)
    if rc != 0:
        return None
    for line in out.splitlines():
        if key in line:
            val = line.split(":", 1)[-1].strip().lower()
            if val in ("true", "1"):
                return True
            if val in ("false", "0"):
                return False
    return None


def _reconcile_meshtastic_tx(cfg: NucleusConfig) -> bool:
    """Push lora.tx_enabled to the radio only when it differs from config.

    "Off" is receive-only: the radio stays powered and keeps RX, only TX stops.
    Best-effort and only runs when meshtasticd is enabled (no daemon = nothing to
    set). Returns True if a change was pushed (caller may need to wait for the
    radio to reboot, like a region change). A `lora.*` change can reboot the
    radio; callers handle that as the init path does.
    """
    if not cfg.meshtastic.enabled:
        return False
    want = cfg.meshtastic.tx_enabled
    current = _meshtastic_get_bool("lora.tx_enabled")
    if current is None or current == want:
        return False  # unreadable (leave alone) or already correct
    _meshtastic_cli(["--set", "lora.tx_enabled", "true" if want else "false"])
    return True


def _chown_reticulum() -> None:
    """Give the rnsd config (and its dir) back to RETI_USER.

    apply() runs as root, but rnsd runs as RETI_USER and must own its config
    directory (0700) and file, or it refuses to read them. Idempotent.
    """
    try:
        ent = pwd.getpwnam(RETI_USER)
    except KeyError:
        return  # user absent (e.g. off-box test host); nothing to own
    import os
    d = RETI_CONFIG.parent
    d.mkdir(parents=True, exist_ok=True)
    os.chown(d, ent.pw_uid, ent.pw_gid)
    d.chmod(0o700)
    if RETI_CONFIG.exists():
        os.chown(RETI_CONFIG, ent.pw_uid, ent.pw_gid)


def _reconcile_reticulum(cfg: NucleusConfig) -> list[str]:
    """Enable/disable rnsd to match reticulum.enabled. Returns units touched."""
    _set_service("rnsd", cfg.reticulum.enabled)
    return ["rnsd"]


def _global_ipv4s() -> list[str]:
    """Every global-scope IPv4 on the box, read live (not from config).

    Phones frequently reach a node by IP rather than the .local name, so the
    cert must carry IP SANs too or HTTPS-by-IP fails validation.
    """
    out = subprocess.run(
        ["ip", "-4", "-o", "addr", "show", "scope", "global"],
        capture_output=True, text=True, check=False,
    ).stdout
    ips: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet":
            ip = parts[3].split("/")[0]
            if ip not in ips:
                ips.append(ip)
    return ips


def _wanted_sans(hostname: str) -> list[str]:
    """SAN set for the web cert: the .local name plus every global IPv4."""
    sans = [f"DNS:{hostname}.local"]
    sans += [f"IP:{ip}" for ip in _global_ipv4s()]
    return sans


def _cert_sans(path: Path) -> list[str]:
    """Read the SAN list from an existing cert, or [] if unreadable/missing."""
    if not path.exists():
        return []
    out = subprocess.run(
        ["openssl", "x509", "-in", str(path), "-noout", "-text"],
        capture_output=True, text=True, check=False,
    ).stdout
    sans: list[str] = []
    grab = False
    for line in out.splitlines():
        s = line.strip()
        if grab:
            for tok in s.split(","):
                tok = tok.strip()
                if tok.startswith(("DNS:", "IP Address:", "IP:")):
                    sans.append(tok.replace("IP Address:", "IP:"))
            break
        if "Subject Alternative Name" in s:
            grab = True
    return sans


def _ensure_web_cert(hostname: str) -> bool:
    """Generate the self-signed web cert when missing or when SANs changed.

    Idempotent: leaves an up-to-date cert untouched. Returns True if it
    (re)generated the cert (so nginx should reload).
    """
    wanted = _wanted_sans(hostname)
    if CERT_CRT.exists() and CERT_KEY.exists():
        if sorted(_cert_sans(CERT_CRT)) == sorted(wanted):
            return False
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
            "-days", "3650",
            "-keyout", str(CERT_KEY),
            "-out", str(CERT_CRT),
            "-subj", f"/CN={hostname}.local",
            "-addext", "subjectAltName=" + ",".join(wanted),
        ],
        check=False,
    )
    CERT_KEY.chmod(0o600)
    return True


def _reload_nginx() -> None:
    """Validate + reload nginx so the vhost/cert changes take effect."""
    if subprocess.run(["nginx", "-t"], capture_output=True).returncode == 0:
        subprocess.run(["systemctl", "reload", "nginx"], check=False)


def _reload_udev() -> None:
    """Reload udev rules and re-apply them so a changed rule file takes effect
    on already-attached devices (udev is not a systemd unit)."""
    subprocess.run(["udevadm", "control", "--reload"], check=False)
    subprocess.run(["udevadm", "trigger", "--subsystem-match=tty"], check=False)


def apply(cfg: NucleusConfig, dry_run: bool = False) -> ApplyResult:
    """Render, write changed files, restart affected units. Idempotent."""
    env = _env()
    ctx = cfg.render_context()
    result = ApplyResult(dry_run=dry_run)
    units_to_restart: set[str] = set()

    for t in TARGETS:
        content = env.get_template(t.template).render(**ctx)
        if dry_run:
            if not (t.dest.exists() and t.dest.read_text() == content):
                result.changed.append(str(t.dest))
                units_to_restart.update(t.units)
            continue
        if _write_if_changed(t.dest, content, t.mode):
            result.changed.append(str(t.dest))
            units_to_restart.update(t.units)

    # meshtasticd is enable/disable-driven (not just restart): reconcile its
    # unit state via _reconcile_meshtastic below, so drop it from the plain
    # restart set to avoid starting a unit we're about to disable.
    units_to_restart.discard("meshtasticd")
    # rnsd is enable/disable-driven too; reconcile below after chowning its
    # config so we never start it against a root-owned file it can't read.
    units_to_restart.discard("rnsd")
    # nucleus-halow is enable/disable-driven and restart-on-conf-change via
    # _reconcile_halow below (it's device-started by udev). Drop it from the plain
    # restart set so a group bounce doesn't start it against a stale conf or when
    # HaLow is disabled; _reconcile_halow owns its lifecycle.
    units_to_restart.discard(HALOW_UNIT)

    # When the mesh is disabled its units are stopped by _reconcile_rf below and
    # must NOT be (re)started by the network-group bounce, or a disabled mesh
    # would come straight back up. Drop them from the restart set, mirroring the
    # meshtasticd/rnsd enable-driven handling above.
    if not cfg.mesh.enabled:
        units_to_restart -= set(MESH_UNITS)

    if dry_run:
        return result

    # Mesh radio must be live before nucleus-mesh starts, so unblock phy1 before
    # the group restart. (Blocking on disable is done by _reconcile_rf, after the
    # units are stopped.) Idempotent — safe to also run inside _reconcile_rf.
    if cfg.mesh.enabled:
        rf.set_iface_blocked(rf.MESH_IFACE, blocked=False)

    if units_to_restart:
        # If any member of the network group changed, bounce the WHOLE group in a
        # fixed order — restarting only the changed unit leaves the others (babeld,
        # smcroute, hostapd, brlan-setup) bound to stale interfaces and multicast
        # memberships, the half-broken-until-reboot symptom. brlan-setup runs after
        # hostapd: it re-enslaves wlan0 into br-lan once the AP is up.
        if units_to_restart & set(NET_GROUP_ORDER):
            ordered = list(NET_GROUP_ORDER)
        else:
            ordered = [u for u in NET_GROUP_ORDER if u in units_to_restart]
        # Never start mesh units while the mesh is disabled (see the discard above).
        if not cfg.mesh.enabled:
            ordered = [u for u in ordered if u not in MESH_UNITS]
        _restart(ordered)
        result.units_restarted = ordered

    # Reconcile the wlan1 mesh RF + units to match mesh.enabled every apply
    # (idempotent): rfkill phy1 and enable/disable nucleus-mesh/babeld/smcroute.
    result.units_restarted += _reconcile_rf(cfg)

    # Reconcile the HaLow service to match halow.enabled every apply (idempotent),
    # and restart it if its supplicant conf / up-script changed (live re-join).
    result.units_restarted += _reconcile_halow(cfg, result.changed)

    # Reconcile meshtasticd + cot-bridge enabled-state every apply (cheap and
    # idempotent), then restart meshtasticd if its rendered config changed.
    result.units_restarted += _reconcile_meshtastic(cfg)
    if str(Path("/etc/meshtasticd/config.yaml")) in result.changed and cfg.meshtastic.enabled:
        _restart(["meshtasticd"])
    # Push lora.tx_enabled to the radio when it differs from config ("off" =
    # receive-only). Best-effort; only acts on a delta so it stays idempotent.
    _reconcile_meshtastic_tx(cfg)

    # Reticulum: ensure rnsd owns its config, reconcile enabled-state, and
    # restart it if its rendered config changed (and it's enabled).
    _chown_reticulum()
    result.units_restarted += _reconcile_reticulum(cfg)
    if str(RETI_CONFIG) in result.changed and cfg.reticulum.enabled:
        _restart(["rnsd"])

    # USB udev rule: if the generated rule changed, reload udev and re-trigger so
    # /dev/nucleus/<role> links are (re)created for already-attached devices.
    if str(USB_UDEV_RULE) in result.changed:
        _reload_udev()
        result.units_restarted.append("udev")

    # nginx reverse proxy: ensure the self-signed cert matches current SANs,
    # enable our vhost, drop the stock default site, and reload if anything
    # changed. All idempotent.
    cert_changed = _ensure_web_cert(cfg.node.hostname)
    NGINX_ENABLED.parent.mkdir(parents=True, exist_ok=True)
    symlink_changed = False
    if not NGINX_ENABLED.is_symlink() or NGINX_ENABLED.resolve() != NGINX_VHOST:
        if NGINX_ENABLED.exists() or NGINX_ENABLED.is_symlink():
            NGINX_ENABLED.unlink()
        NGINX_ENABLED.symlink_to(NGINX_VHOST)
        symlink_changed = True
    default_removed = False
    if NGINX_DEFAULT.is_symlink() or NGINX_DEFAULT.exists():
        NGINX_DEFAULT.unlink()
        default_removed = True
    if (str(NGINX_VHOST) in result.changed or str(NGINX_HTPASSWD) in result.changed
            or cert_changed or symlink_changed or default_removed):
        _reload_nginx()
        result.units_restarted.append("nginx")

    return result
