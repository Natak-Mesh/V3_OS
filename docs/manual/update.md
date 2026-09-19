# System Update page

Compares the installed (running) version with the git remote and launches the
node update. Git and live state are kept separate — the running version only
changes after the update pulls, reinstalls the venv and restarts services.

## Status

| State | Meaning |
|-------|---------|
| up to date | Installed matches the remote (0 commits behind). |
| update available (N commits behind) | A newer version exists; **Update now** is offered. |
| local ahead / diverged | Local history is ahead of or diverged from the remote. |
| offline — cannot reach git remote | No connectivity to the remote. |

The detail line shows the installed version and head vs the available version and
head. If the working tree has uncommitted changes it shows a warning and the
update is **blocked** until they are resolved.

## Actions

- **Update now (pulls, reinstalls, restarts)** — shown only when an update is
  available and the tree is clean. Prompts for confirmation, then runs the
  update and streams the log below. The update restarts nucleusd mid-run, so the
  web UI may briefly disconnect; the on-disk status survives the restart, so
  transient fetch failures are tolerated while polling progress.
- **Refresh** — re-check the version status.
