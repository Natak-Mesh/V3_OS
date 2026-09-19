"""Node self-update: version comparison + orchestration of nucleus-update.sh.

The update flow keeps the git checkout and the live system separate: the venv
holds a frozen copy of the package (a plain, non-editable `pip install`), so a
`git pull` in the repo does not touch the running service until the update
script re-runs install.sh (which rebuilds the venv) and restarts nucleusd.

version_info() reports what the running service is (installed) vs what the git
remote would give (available). "installed" is the venv package version
(__version__), NOT the repo's VERSION file — the installed value is the truth
about what is actually running.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import __version__

# The git checkout that is the source of updates. Overridable for tests.
REPO_DIR = Path("/home/natak/V3_OS")
# The branch we track / fast-forward against.
BRANCH = "main"

# The update script (installed by install.sh) and its on-disk state files. The
# script writes its REAL outcome here because it restarts nucleusd mid-run,
# wiping any in-memory state; these files are the source of truth for progress.
UPDATE_SCRIPT = "/opt/nucleus/bin/nucleus-update.sh"
UPDATE_LOG = "/var/log/nucleus-update.log"
UPDATE_STATUS = "/var/log/nucleus-update.status"

# Human-readable outcome for each nucleus-update.sh exit code.
RC_MESSAGES = {
    0: "Update succeeded. A reboot is recommended to apply all changes.",
    1: "Already up to date — no new code pulled.",
    2: "Offline — git remote unreachable. Update aborted.",
    3: "Local changes present (dirty working tree) — update stopped.",
    4: "git pull failed.",
    5: "install.sh failed.",
    6: "nucleusctl apply failed.",
    7: "Service restart failed.",
    8: "Environment error (repo missing or not a git repo).",
}


def _git(*args: str, timeout: int = 25) -> subprocess.CompletedProcess:
    """Run a git command inside REPO_DIR, capturing output (never raises).

    nucleusd runs as root while the repo is owned by natak; without a
    safe.directory exception git refuses every operation ("dubious ownership")
    before it ever reaches the network.
    """
    return subprocess.run(
        ["git", "-c", f"safe.directory={REPO_DIR}", "-C", str(REPO_DIR), *args],
        capture_output=True, text=True, check=False, timeout=timeout,
    )



def version_info() -> dict:
    """Compare the installed (running) version with the git remote.

    Returns a dict the web UI renders:
      installed  running package version (__version__)
      available  VERSION on origin/<branch> (None if offline/unknown)
      behind     commits the local checkout is behind origin/<branch>
      dirty      True if the working tree has uncommitted changes
      offline    True if the remote could not be reached
      local_head/remote_head  short commit ids (for display)
    """
    info = {
        "installed": __version__,
        "available": None,
        "behind": None,
        "dirty": False,
        "offline": False,
        "local_head": None,
        "remote_head": None,
        "error": None,
    }

    if not (REPO_DIR / ".git").exists():
        info["error"] = f"{REPO_DIR} is not a git repository"
        return info

    # Dirty working tree (uncommitted local changes block a clean update).
    st = _git("status", "--porcelain")
    info["dirty"] = bool(st.stdout.strip())

    info["local_head"] = _git("rev-parse", "--short", "HEAD").stdout.strip() or None

    # Contact the remote. Distinguish a genuine network failure (offline) from
    # a local git/config error (e.g. dubious ownership) so the UI reports the
    # right cause instead of blaming the network.
    fetched = _git("fetch", "--quiet", "origin", BRANCH, timeout=30)
    if fetched.returncode != 0:
        err = (fetched.stderr or "git fetch failed").strip()
        info["error"] = err
        low = err.lower()
        info["offline"] = not any(s in low for s in (
            "dubious ownership", "safe.directory", "not a git repository",
            "permission denied",
        ))
        return info

    ref = f"origin/{BRANCH}"
    info["remote_head"] = _git("rev-parse", "--short", ref).stdout.strip() or None

    # Available version = the VERSION file on the remote branch.
    ver = _git("show", f"{ref}:VERSION")
    if ver.returncode == 0:
        info["available"] = ver.stdout.strip()

    # How many commits the local checkout is behind the remote branch.
    count = _git("rev-list", "--count", f"HEAD..{ref}")
    if count.returncode == 0 and count.stdout.strip().isdigit():
        info["behind"] = int(count.stdout.strip())

    return info


def start_update() -> dict:
    """Launch nucleus-update.sh detached via systemd-run.

    systemd-run detaches the script from the web process, so nucleusd can be
    restarted by the script without killing the update. Progress is tracked via
    the on-disk status/log files, not this process.
    """
    if not Path(UPDATE_SCRIPT).exists():
        return {"started": False, "detail": f"update script missing: {UPDATE_SCRIPT}"}

    # Already running? refuse to launch a second copy.
    if progress().get("status") == "running":
        return {"started": False, "detail": "an update is already running"}

    r = subprocess.run(
        ["sudo", "-n", "systemd-run", "--unit=nucleus-update",
         "--collect", UPDATE_SCRIPT],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return {"started": False, "detail": (r.stderr or "systemd-run failed").strip()}
    return {"started": True}


def _parse_status(text: str) -> dict:
    """Parse the key=value status file the update script writes."""
    out: dict = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def progress() -> dict:
    """Return current update progress from the on-disk status + log files."""
    result = {"status": "idle", "rc": None, "message": None, "log": []}

    try:
        status_text = Path(UPDATE_STATUS).read_text()
    except OSError:
        status_text = ""

    if status_text:
        st = _parse_status(status_text)
        result["status"] = st.get("status", "idle")
        if "rc" in st and st["rc"].lstrip("-").isdigit():
            rc = int(st["rc"])
            result["rc"] = rc
            result["message"] = RC_MESSAGES.get(rc, f"Finished with code {rc}.")

    try:
        # Cap the log we return to the last 500 lines.
        lines = Path(UPDATE_LOG).read_text().splitlines()
        result["log"] = lines[-500:]
    except OSError:
        pass

    return result
