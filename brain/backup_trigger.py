"""Fires the off-Pi encrypted backup (see scripts/backup_memory.sh) right
after a household_memory/ write, so a dead Pi loses at most the last few
minutes of facts rather than waiting for the next scheduled run.

Best-effort and non-blocking -- a backup hiccup must never slow down or break
a conversation turn -- and silently no-ops anywhere systemd or the unit isn't
installed (a dev Mac, a fresh clone before the systemd units are set up).
"""
from __future__ import annotations

import subprocess


def trigger() -> None:
    try:
        subprocess.Popen(
            ["systemctl", "--user", "start", "pi-memory-backup.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):
        pass
