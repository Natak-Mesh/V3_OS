#!/bin/bash
# ============================================================
# Nucleus V3 OS — node update script.
#
# Pulls the latest code from git and re-runs the idempotent install flow,
# preserving the separation between the git checkout and the live system: the
# venv holds a frozen copy of the package (non-editable pip install), so this
# script's `git pull` does not affect the running service until install.sh
# rebuilds the venv and nucleusd is restarted.
#
# Order of operations:
#   1. Pre-flight WAN reachability (abort if offline)
#   2. Record current git HEAD
#   3. Refuse to continue on a dirty working tree (uncommitted edits)
#   4. git pull --ff-only  (exit 1 if already up to date)
#   5. install.sh          (rebuilds venv from the new code, idempotent)
#   6. nucleusctl apply    (normalize config + re-render system configs)
#   6b. restart nucleus-voice / nucleus-messaging (config-reading daemons)
#   7. systemctl restart nucleusd.service  (edits are not live until this)
#
# This script does NOT reboot. Reboot is a separate operator action.
#
# Exit codes (mirrored in nucleusd/update.py RC_MESSAGES):
#   0 updated  1 up-to-date  2 offline  3 dirty tree  4 pull failed
#   5 install.sh failed  6 apply failed  7 restart failed  8 env error
# ============================================================

REPO_DIR="${NUCLEUS_REPO_DIR:-/home/natak/V3_OS}"
LOG_FILE="/var/log/nucleus-update.log"
STATUS_FILE="/var/log/nucleus-update.status"

# --- logging: fall back to a user-writable path if /var/log is not writable --
if ! touch "$LOG_FILE" 2>/dev/null; then
    LOG_FILE="$HOME/nucleus-update.log"
fi
: > "$LOG_FILE"    # truncate: each run's log stands alone
if ! touch "$STATUS_FILE" 2>/dev/null; then
    STATUS_FILE="$(dirname "$LOG_FILE")/nucleus-update.status"
fi
chmod 644 "$LOG_FILE" "$STATUS_FILE" 2>/dev/null || true

# Single EXIT trap captures EVERY exit path, even if step 7 restarts nucleusd
# (which spawned us). $? here is the script's real exit status.
on_exit() {
    local rc=$?
    printf 'status=finished\nrc=%s\nfinished=%s\n' "$rc" "$(date '+%s')" \
        > "$STATUS_FILE" 2>/dev/null || true
}
trap on_exit EXIT
printf 'status=running\npid=%s\nstarted=%s\n' "$$" "$(date '+%s')" \
    > "$STATUS_FILE" 2>/dev/null || true

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
fail() { local code="$1"; shift; log "ERROR: $*"; log "===== update FAILED (exit $code) ====="; exit "$code"; }

log "===== nucleus-update started ====="
log "repo: $REPO_DIR"

# --- environment checks ------------------------------------------------------
[ -d "$REPO_DIR" ] || fail 8 "repo directory not found: $REPO_DIR"
cd "$REPO_DIR" || fail 8 "cannot cd into repo: $REPO_DIR"
# This script runs as root (via systemd-run) but the repo is owned by natak;
# without this exception git refuses every op with "dubious ownership".
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$REPO_DIR"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail 8 "$REPO_DIR is not a git repository"

# --- step 1: pre-flight WAN reachability ------------------------------------
log "checking network reachability to git remote..."
if ! timeout 20 git ls-remote origin >/dev/null 2>&1; then
    log "git remote unreachable"
    log "===== update ABORTED (offline) ====="
    exit 2
fi
log "network OK"

# --- step 2: record HEAD -----------------------------------------------------
BEFORE_HEAD="$(git rev-parse HEAD 2>/dev/null)"
log "current git HEAD: $BEFORE_HEAD"

# --- step 3: dirty working tree check ---------------------------------------
if [ -n "$(git status --porcelain)" ]; then
    log "working tree has uncommitted changes:"
    git status --short | tee -a "$LOG_FILE"
    fail 3 "repository has local modifications - stopping. Resolve them, then re-run."
fi

# --- step 4: git pull --------------------------------------------------------
log "pulling latest code..."
if ! git pull --ff-only 2>&1 | tee -a "$LOG_FILE"; then
    fail 4 "git pull failed"
fi
AFTER_HEAD="$(git rev-parse HEAD 2>/dev/null)"
log "git HEAD after pull: $AFTER_HEAD"
if [ "$BEFORE_HEAD" = "$AFTER_HEAD" ]; then
    log "already up to date - no new code pulled"
    log "===== update finished (no changes) ====="
    exit 1
fi

# --- step 5: install.sh (rebuilds the venv from the new code) ----------------
log "running install.sh..."
if ! ./install.sh 2>&1 | tee -a "$LOG_FILE"; then
    fail 5 "install.sh failed"
fi

# --- step 6: nucleusctl apply -----------------------------------------------
log "running nucleusctl apply..."
if ! nucleusctl apply 2>&1 | tee -a "$LOG_FILE"; then
    fail 6 "nucleusctl apply failed"
fi

# --- step 6b: restart config-reading app daemons ----------------------------
# nucleus-voice / nucleus-messaging read /etc/nucleus/config.yaml directly (not
# through nucleusd). install.sh already restarted them, but that ran BEFORE step
# 6's `nucleusctl apply` normalized the config (persisting new schema defaults),
# so restart them again here to pick up the freshly-merged keys in this same run.
# Non-fatal: a failure here shouldn't abort an otherwise-successful update.
log "restarting config-reading app daemons (nucleus-voice, nucleus-messaging)..."
systemctl restart nucleus-voice.service nucleus-messaging.service 2>&1 | tee -a "$LOG_FILE" \
    || log "WARNING: app daemon restart failed (non-fatal); a reboot will apply changes"

# --- step 7: restart nucleusd (edits are not live until this) ---------------
log "restarting nucleusd.service..."
if ! systemctl restart nucleusd.service 2>&1 | tee -a "$LOG_FILE"; then
    fail 7 "nucleusd restart failed"
fi

log "update applied. A reboot is recommended to apply all changes."
log "===== update SUCCEEDED ====="
exit 0
