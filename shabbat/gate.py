#!/usr/bin/env python3
"""Shabbat/Yom Tov gate checker -- meant to run periodically (every minute) via systemd timer.

Two independent responsibilities, per the design spec (docs/specs/shabbat-gating.md):

1. Gate enforcement -- idempotent, recomputed fresh every run from the current time and
   cached schedule. Never depends on having caught a prior run's transition, so a missed
   run (e.g. the Pi was off) self-corrects the moment this next runs.
2. Announcement playback -- the warnings/entrance/exit messages, which must fire exactly
   once each. Uses a small state file to avoid repeat-firing on every periodic check.

Fails closed: any uncertainty about the clock or the cached schedule's trustworthiness
gates the device OFF, never on.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from audio_check.devices import find_output_device
from audio_check.errors import AudioCheckError
from audio_check.player import play_wav

from .config import ShabbatConfig, load_config
from .hebcal_client import get_data
from .ntp import is_clock_trustworthy
from .schedule import build_windows, is_gated, scheduled_events

ANNOUNCEMENT_LOOKBACK = timedelta(minutes=2)  # don't fire events older than this if a run was missed
STATE_PRUNE_AFTER_DAYS = 60


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    # --user: both this checker and the wake-word daemon run as user-level systemd
    # units (not system units), so they share the same PipeWire audio session --
    # a system-level (root) unit would not have access to that per-user session.
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _service_is_active(unit: str) -> bool:
    result = _systemctl("is-active", unit)
    return result.stdout.strip() == "active"


def enforce_gate(config: ShabbatConfig, should_gate: bool) -> None:
    active = _service_is_active(config.systemd_unit)
    if should_gate and active:
        print(f"Gating: stopping {config.systemd_unit}")
        _systemctl("stop", config.systemd_unit)
    elif not should_gate and not active:
        print(f"Un-gating: starting {config.systemd_unit}")
        _systemctl("start", config.systemd_unit)


def _load_fired_ids(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    try:
        return set(json.loads(state_path.read_text()).get("fired", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_fired_ids(state_path: Path, fired: set[str], now: datetime) -> None:
    # Prune ids old enough that their embedded timestamp can never recur.
    cutoff = now - timedelta(days=STATE_PRUNE_AFTER_DAYS)
    pruned = {fid for fid in fired if _extract_timestamp(fid) is None or _extract_timestamp(fid) > cutoff}
    state_path.write_text(json.dumps({"fired": sorted(pruned)}))


def _extract_timestamp(event_id: str) -> datetime | None:
    try:
        return datetime.fromisoformat(event_id.split("_")[0])
    except ValueError:
        return None


def _message_name(event) -> str:
    occasion = "yomtov" if event.is_yomtov else "shabbat"
    if event.kind == "warning":
        return f"warning_{event.minutes_before}_{occasion}"
    if event.kind == "entrance":
        return f"candle_{occasion}"
    return f"havdalah_{occasion}"


def fire_due_announcements(config: ShabbatConfig, events, now: datetime) -> None:
    fired = _load_fired_ids(config.state_path)
    due = [e for e in events if now - ANNOUNCEMENT_LOOKBACK <= e.at <= now and e.event_id not in fired]
    if not due:
        return

    try:
        out_device = find_output_device(None)
    except AudioCheckError as exc:
        print(f"Error selecting output device for announcement: {exc}", file=sys.stderr)
        return

    for event in sorted(due, key=lambda e: e.at):
        wav = config.message_wav(_message_name(event))
        print(f"Playing announcement: {wav.name}")
        try:
            play_wav(wav, out_device)
        except AudioCheckError as exc:
            print(f"Announcement playback failed: {exc}", file=sys.stderr)
        fired.add(event.event_id)

    _save_fired_ids(config.state_path, fired, now)


def main() -> None:
    config = load_config()
    now = datetime.now().astimezone()

    if not is_clock_trustworthy():
        print("Clock not NTP-synced -- failing closed (gating on) until sync is confirmed.")
        enforce_gate(config, should_gate=True)
        return

    items, status = get_data(config)
    if items is None:
        print("No trustworthy zmanim data available -- failing closed (gating on).")
        enforce_gate(config, should_gate=True)
        return

    if status != "fresh":
        print(f"Zmanim data status: {status}")

    windows = build_windows(items)
    should_gate = is_gated(windows, now)
    enforce_gate(config, should_gate)

    events = scheduled_events(windows, config.warning_offsets_minutes)
    fire_due_announcements(config, events, now)


if __name__ == "__main__":
    main()
