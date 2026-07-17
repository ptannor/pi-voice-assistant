"""Confirms the system clock is trustworthy before it's used for any
Shabbat/Yom Tov gating decision.

Queries a real NTP server directly rather than asking the OS whether it
considers itself synced -- there's no single way to ask that across
platforms (systemd-timesyncd/chrony on Linux, w32time on Windows, ntpd/sntp
on macOS all differ, and some hosts run none of them), but querying NTP
directly over the network works identically on Mac, Linux, and Windows.
"""
from __future__ import annotations

import os

import ntplib

NTP_SERVER = "pool.ntp.org"
NTP_TIMEOUT_SECONDS = 5
MAX_CLOCK_DRIFT_SECONDS = 5.0  # beyond this, don't trust the local clock


def is_clock_trustworthy() -> bool:
    """Fail closed (return False) whenever this can't be verified.

    SHABBAT_SKIP_NTP_CHECK=1 bypasses this for local development on
    machines/networks where the NTP query itself is unreliable (e.g. a
    firewalled dev network) -- never set this on a real deployment.
    """
    if os.environ.get("SHABBAT_SKIP_NTP_CHECK") == "1":
        return True
    try:
        response = ntplib.NTPClient().request(
            NTP_SERVER, version=3, timeout=NTP_TIMEOUT_SECONDS
        )
    except (ntplib.NTPException, OSError):
        return False
    return abs(response.offset) <= MAX_CLOCK_DRIFT_SECONDS
