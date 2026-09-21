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

## Config default merge

Updates ship new code, but `install.sh` never touches an existing
`/etc/nucleus/config.yaml` (it seeds that file only if absent). Without a merge
step, a new feature's config keys never reach an already-provisioned node, so the
feature stays off even after a successful update. This gap is closed by
`config.normalize()`:

- `nucleusctl apply` (already run by the update, as root) calls
  `config.normalize()` first: it loads the live config — the schema fills in any
  key the file omits with that key's default — and writes the fully-populated
  model back. Any key **missing** from the live file gains its schema default;
  operator-set values are **never** changed (they override defaults on load).
- The write goes through `config.py` — the one module that does YAML I/O — using
  its atomic save, so a crash mid-update cannot brick boot.
- Operators override any merged default afterwards via the CONFIG page / API,
  exactly as before.
- The rewrite is schema-normalized YAML, so hand-written comments/ordering in the
  live file are not preserved. The repo `config/config.yaml` keeps the commented
  reference copy.

### How this obeys the README pattern (Option A)

- **One contract.** `schema.py` is the single source of truth for defaults;
  normalize just persists what the schema already computes on load.
- **Config stays operator-owned state.** Normalize only fills gaps; it never
  clobbers a value an operator set — consistent with "seed only if absent".
- **Idempotent apply.** Re-running with nothing missing rewrites nothing
  (`normalize()` returns False), so it is safe to run on every apply.

### Result: the update path is self-contained

1. User clicks **Update now** in the web UI.
2. Script git-pulls the latest repo.
3. `install.sh` installs apt packages + pip deps (new features' system deps).
4. `nucleusctl apply` merges missing config defaults, then renders configs.
5. `nucleus-voice` / `nucleus-messaging` restart (they read config.yaml
   directly, so they must restart *after* the merge to pick up new keys in the
   same run), then `nucleusd` restarts.

No post-update manual commands are needed to bring a new feature online on an
existing node.
