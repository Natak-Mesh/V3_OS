"""Imperative Tailscale control — a thin wrapper over the `tailscale` CLI.

Tailscale is deliberately NOT part of the config.yaml -> apply pipeline: its
state (which tailnet, logged-in/out, advertised routes) lives in tailscaled's
own state store, not in our declarative config. The web UI drives it directly
through the small set of verbs below, so there is exactly one place that shells
out to the CLI and one place that parses its JSON.

Pure-ish: every function shells out to `tailscale`, but each returns plain data
(or raises TailscaleError), so the router/tests can exercise them with a mocked
`_run`.
"""

from __future__ import annotations

import json
import shutil
import subprocess

# tailscaled runs system-wide; nucleusd runs as root (no User= in its unit), so
# the CLI needs no sudo. Fixed binary name keeps the mock surface tiny.
TAILSCALE = "tailscale"
_TIMEOUT = 30.0  # `tailscale up` can block on the control plane; keep generous.


class TailscaleError(RuntimeError):
    """A tailscale CLI invocation failed (non-zero exit or missing binary)."""


def _run(args: list[str], timeout: float = _TIMEOUT) -> subprocess.CompletedProcess:
    """Run `tailscale <args>`; raise TailscaleError if the binary is absent.

    Never raises on a non-zero exit (callers inspect returncode/stderr), but a
    missing binary or timeout is a hard error the caller can surface as 503.
    """
    if shutil.which(TAILSCALE) is None:
        raise TailscaleError("tailscale is not installed")
    try:
        return subprocess.run(
            [TAILSCALE, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise TailscaleError(f"tailscale {' '.join(args)} timed out") from e
    except OSError as e:
        raise TailscaleError(str(e)) from e


def _stopped(installed: bool) -> dict:
    return {"installed": installed, "running": False,
            "state": "NotInstalled" if not installed else "Stopped",
            "logged_in": False, "self_ip": "", "tailnet": "",
            "hostname": "", "peers": 0}


def status() -> dict:
    """Return a compact connection summary parsed from `tailscale status --json`.

    Shape (stable contract for the UI): installed, running, state (BackendState),
    logged_in, self_ip (100.x), tailnet, hostname, peers.
    """
    if shutil.which(TAILSCALE) is None:
        return _stopped(False)

    cp = _run(["status", "--json"])
    if cp.returncode != 0 or not cp.stdout.strip():
        return _stopped(True)
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError:
        return _stopped(True)

    state = data.get("BackendState", "") or ""
    self_ = data.get("Self") or {}
    ips = self_.get("TailscaleIPs") or []
    self_ip = next((ip for ip in ips if ip.startswith("100.")), ips[0] if ips else "")
    # CurrentTailnet is present when logged in; MagicDNSSuffix is a good fallback.
    tailnet = ""
    ct = data.get("CurrentTailnet")
    if isinstance(ct, dict):
        tailnet = ct.get("Name", "") or ct.get("MagicDNSSuffix", "") or ""
    if not tailnet:
        tailnet = data.get("MagicDNSSuffix", "") or ""
    return {
        "installed": True,
        "running": state == "Running",
        "state": state or "Unknown",
        "logged_in": state not in ("NeedsLogin", "NoState", ""),
        "self_ip": self_ip,
        "tailnet": tailnet,
        "hostname": self_.get("HostName", "") or "",
        "peers": len(data.get("Peer") or {}),
    }


def up(authkey: str | None = None, login_server: str | None = None) -> dict:
    """Bring the connection up.

    With an authkey we join non-interactively. Without one, `tailscale up` prints
    a login URL to stderr and (with --timeout) returns; we surface that URL so the
    UI can show it for browser login.

    Returns {"connected": bool, "auth_url": str | ""}.
    """
    args = ["up"]
    if login_server:
        args.append(f"--login-server={login_server}")
    if authkey:
        args.append(f"--authkey={authkey}")
    else:
        # Don't block the request forever waiting for a browser login; return the
        # URL instead. tailscale prints the URL, then exits non-zero on timeout.
        args.append("--timeout=1s")

    cp = _run(args)
    if cp.returncode == 0:
        return {"connected": True, "auth_url": ""}

    url = _extract_auth_url(cp.stdout + "\n" + cp.stderr)
    if url:
        return {"connected": False, "auth_url": url}
    raise TailscaleError((cp.stderr or cp.stdout or "tailscale up failed").strip())


def down() -> dict:
    """Disconnect from the tailnet (stays logged in). Idempotent."""
    cp = _run(["down"])
    if cp.returncode != 0:
        raise TailscaleError((cp.stderr or "tailscale down failed").strip())
    return {"connected": False}


def logout() -> dict:
    """Log out of the current tailnet (drops the auth session)."""
    cp = _run(["logout"])
    if cp.returncode != 0:
        raise TailscaleError((cp.stderr or "tailscale logout failed").strip())
    return {"logged_in": False}


def accounts() -> list[dict]:
    """List logged-in tailnet profiles from `tailscale switch --list`.

    Output is a fixed-width table with a header row, e.g.:
        ID     Tailnet          Account
        abc123 example.com      user@example.com*
    A trailing '*' on the account marks the active profile. Returns
    [{"id","tailnet","account","active"}]. Empty list if only one/zero profiles.
    """
    cp = _run(["switch", "--list"])
    if cp.returncode != 0:
        return []
    out = []
    for ln in cp.stdout.splitlines():
        if not ln.strip():
            continue
        parts = ln.split()
        if not parts or parts[0].upper() == "ID":  # skip header
            continue
        acct = parts[-1]
        active = acct.endswith("*")
        if active:
            acct = acct[:-1]
        out.append({
            "id": parts[0],
            "tailnet": parts[1] if len(parts) >= 3 else "",
            "account": acct,
            "active": active,
        })
    return out


def switch(account: str) -> dict:
    """Switch to an already-authenticated tailnet profile by id or account name."""
    if not account:
        raise TailscaleError("account is required")
    cp = _run(["switch", account])
    if cp.returncode != 0:
        raise TailscaleError((cp.stderr or "tailscale switch failed").strip())
    return {"switched": account}


def _extract_auth_url(text: str) -> str:
    """Pull the https login URL out of `tailscale up` output, if present."""
    for tok in text.split():
        if tok.startswith("https://") and (
            "login" in tok or "/a/" in tok or "controlplane" in tok or "tailscale" in tok
        ):
            return tok.strip().rstrip(".")
    return ""

