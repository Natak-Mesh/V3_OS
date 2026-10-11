"""Render -> diff -> restart engine.

This is the heart of the "config.yaml is the only thing you edit" model. It
renders every Jinja2 template against the validated config, writes only the
files whose content actually changed, and restarts only the systemd units whose
inputs changed. Idempotent: re-running with no config change touches nothing.

Importable as a plain module (used by the CLI), so the apply logic exists in
exactly one place.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

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
    Target("wpa_supplicant-mesh.conf.j2", Path("/etc/wpa_supplicant/wpa_supplicant-mesh.conf"), ("nucleus-mesh",), 0o600),
    Target("nucleus-mesh-up.sh.j2", Path("/opt/nucleus/bin/nucleus-mesh-up.sh"), ("nucleus-mesh",), 0o755),
    Target("babeld.conf.j2", Path("/etc/babeld.conf"), ("babeld",)),
    Target("smcroute.conf.j2", Path("/etc/smcroute.conf"), ("smcroute",)),
    Target("hostapd.conf.j2", Path("/etc/hostapd/hostapd.conf"), ("hostapd", "brlan-setup")),
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

    if dry_run:
        return result

    if units_to_restart:
        # Deterministic, dependency-friendly restart order. brlan-setup must
        # run last: it re-enslaves wlan0 into br-lan after hostapd is up.
        order = ["systemd-networkd", "nucleus-mesh", "babeld", "smcroute", "hostapd", "brlan-setup"]
        ordered = [u for u in order if u in units_to_restart]
        _restart(ordered)
        result.units_restarted = ordered

    return result
