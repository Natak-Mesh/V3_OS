"""Render -> diff -> restart engine.

This is the heart of the "config.yaml is the only thing you edit" model. It
renders every Jinja2 template against the validated config, writes only the
files whose content actually changed, and restarts only the systemd units whose
inputs changed. Idempotent: re-running with no config change touches nothing.

Importable as a plain module (used by both the CLI and the REST API), so the
apply logic exists in exactly one place.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# nginx reverse-proxy paths (port-less .local + by-IP access to the :8080 UI).
NGINX_VHOST = Path("/etc/nginx/sites-available/nucleus")
NGINX_ENABLED = Path("/etc/nginx/sites-enabled/nucleus")
NGINX_DEFAULT = Path("/etc/nginx/sites-enabled/default")
CERT_DIR = Path("/etc/nucleus/certs")
CERT_CRT = CERT_DIR / "nucleus-web.crt"
CERT_KEY = CERT_DIR / "nucleus-web.key"

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .schema import NucleusConfig

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


TARGETS: list[Target] = [
    Target("networkd/20-br-lan.netdev.j2", Path("/etc/systemd/network/20-br-lan.netdev"), ("systemd-networkd",)),
    Target("networkd/21-br-lan.network.j2", Path("/etc/systemd/network/21-br-lan.network"), ("systemd-networkd",)),
    Target("networkd/30-wlan0.network.j2", Path("/etc/systemd/network/30-wlan0.network"), ("systemd-networkd",)),
    Target("networkd/40-eth0.network.j2", Path("/etc/systemd/network/40-eth0.network"), ("systemd-networkd",)),
    Target("networkd/10-wlan1.network.j2", Path("/etc/systemd/network/10-wlan1.network"), ("systemd-networkd",)),
    Target("wpa_supplicant-mesh.conf.j2", Path("/etc/wpa_supplicant/wpa_supplicant-mesh.conf"), ("nucleus-mesh",), 0o600),
    Target("nucleus-mesh-up.sh.j2", Path("/opt/nucleus/bin/nucleus-mesh-up.sh"), ("nucleus-mesh",), 0o755),
    Target("babeld.conf.j2", Path("/etc/babeld.conf"), ("babeld",)),
    Target("smcroute.conf.j2", Path("/etc/smcroute.conf"), ("smcroute",)),
    Target("hostapd.conf.j2", Path("/etc/hostapd/hostapd.conf"), ("hostapd",)),
    Target("meshtasticd-config.yaml.j2", Path("/etc/meshtasticd/config.yaml"), ("meshtasticd",)),
    # nginx reverse proxy — reload (not restart) handled specially in apply().
    Target("nginx-nucleus.conf.j2", NGINX_VHOST, ()),
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


def _restart(units: list[str]) -> None:
    if not units:
        return
    # networkd is reconfigured, everything else restarted.
    for unit in units:
        if unit == "systemd-networkd":
            subprocess.run(["networkctl", "reload"], check=False)
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

    if dry_run:
        return result

    if units_to_restart:
        # Deterministic, dependency-friendly restart order.
        order = ["systemd-networkd", "nucleus-mesh", "babeld", "smcroute", "hostapd"]
        ordered = [u for u in order if u in units_to_restart]
        _restart(ordered)
        result.units_restarted = ordered

    # Reconcile meshtasticd + cot-bridge enabled-state every apply (cheap and
    # idempotent), then restart meshtasticd if its rendered config changed.
    result.units_restarted += _reconcile_meshtastic(cfg)
    if str(Path("/etc/meshtasticd/config.yaml")) in result.changed and cfg.meshtastic.enabled:
        _restart(["meshtasticd"])

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
    if str(NGINX_VHOST) in result.changed or cert_changed or symlink_changed or default_removed:
        _reload_nginx()
        result.units_restarted.append("nginx")

    return result
