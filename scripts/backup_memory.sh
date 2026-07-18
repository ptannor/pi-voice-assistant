#!/usr/bin/env bash
# Pushes an encrypted copy of household_memory/ (core.txt, reference/,
# vault.enc) to a remote the household controls, via an rclone "crypt" remote
# so everything is encrypted client-side before it ever leaves the Pi -- even
# the already-encrypted vault.enc is just harmlessly double-wrapped.
#
# Run by systemd/pi-memory-backup.service, both on a daily timer
# (pi-memory-backup.timer) and right after any memory/vault write (see
# brain/backup_trigger.py) for near-zero data loss if the Pi itself dies.
#
# One-time setup (not done by this script): `rclone config` a crypt remote
# named $MEMORY_BACKUP_REMOTE (default below) wrapping a real remote (e.g.
# Backblaze B2 or a Google Drive folder). See docs/memory-backup.md for the
# full runbook, including where the rclone crypt password and
# MEMORY_VAULT_KEY must be stored (off this Pi -- neither is included in this
# backup, see that doc).
#
# Safe to run often and to re-run after a failure -- `rclone sync` is
# idempotent. No-op (not an error) if rclone or the remote isn't configured
# yet, matching this repo's tolerance for not-yet-set-up optional services
# (see brain/spotify.py, brain/gcal.py).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEMORY_DIR="$REPO_ROOT/household_memory"
REMOTE="${MEMORY_BACKUP_REMOTE:-mendy-backup}:mendy-memory"

if ! command -v rclone >/dev/null 2>&1; then
    echo "backup_memory: rclone not installed -- skipping (see docs/memory-backup.md)."
    exit 0
fi

if [ ! -d "$MEMORY_DIR" ]; then
    echo "backup_memory: no household_memory/ directory yet -- nothing to back up."
    exit 0
fi

if ! rclone listremotes | grep -q "^${MEMORY_BACKUP_REMOTE:-mendy-backup}:$"; then
    echo "backup_memory: rclone remote '${MEMORY_BACKUP_REMOTE:-mendy-backup}' not configured -- skipping (see docs/memory-backup.md)."
    exit 0
fi

rclone sync "$MEMORY_DIR" "$REMOTE" --fast-list
echo "backup_memory: synced household_memory/ to $REMOTE"
