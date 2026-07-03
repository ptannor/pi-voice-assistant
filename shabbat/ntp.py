from __future__ import annotations

import os
import subprocess


def is_clock_trustworthy() -> bool:
    """Fail closed (return False) whenever this can't be verified.

    SHABBAT_SKIP_NTP_CHECK=1 bypasses this for local development on machines
    without systemd (e.g. macOS) -- never set this on the Pi.
    """
    if os.environ.get("SHABBAT_SKIP_NTP_CHECK") == "1":
        return True
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "yes"
