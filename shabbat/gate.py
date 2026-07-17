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
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psutil

from audio_check.devices import find_output_device
from audio_check.errors import AudioCheckError
from audio_check.player import play_wav

from .config import ShabbatConfig, load_config
from .hebcal_client import get_data
from .ntp import is_clock_trustworthy
from .schedule import build_windows, is_gated, scheduled_events

ANNOUNCEMENT_LOOKBACK = timedelta(minutes=2)  # don't fire events older than this if a run was missed
STATE_PRUNE_AFTER_DAYS = 60

REPO_ROOT = Path(__file__).parent.parent
# Only used on platforms without systemd (see enforce_gate) -- wake_word_daemon.py
# writes its own pid here at startup so this checker has something durable to
# test liveness against/terminate, rather than scanning the whole process list.
DAEMON_PIDFILE = REPO_ROOT / "wake_word_daemon.pid"
DAEMON_SCRIPT = REPO_ROOT / "wake_word_daemon.py"


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    # --user: both this checker and the wake-word daemon run as user-level systemd
    # units (not system units), so they share the same PipeWire audio session --
    # a system-level (root) unit would not have access to that per-user session.
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _service_is_active(unit: str) -> bool:
    result = _systemctl("is-active", unit)
    return result.stdout.strip() == "active"


def _daemon_pid() -> int | None:
    if not DAEMON_PIDFILE.exists():
        return None
    try:
        return int(DAEMON_PIDFILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _daemon_is_running() -> bool:
    pid = _daemon_pid()
    return pid is not None and psutil.pid_exists(pid)


def _stop_daemon() -> None:
    pid = _daemon_pid()
    if pid is not None:
        try:
            psutil.Process(pid).terminate()
        except psutil.NoSuchProcess:
            pass
    DAEMON_PIDFILE.unlink(missing_ok=True)


def _start_daemon() -> None:
    subprocess.Popen(
        [sys.executable, str(DAEMON_SCRIPT)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # wake_word_daemon.py writes DAEMON_PIDFILE itself once it's actually running.


def enforce_gate(config: ShabbatConfig, should_gate: bool) -> None:
    # Prefer systemd where it's actually available (the real Pi deployment,
    # see systemd/pi-voice-assistant.service) -- it also gets crash-restart
    # and start-at-boot for free, which the psutil fallback below doesn't
    # attempt to replicate. Elsewhere (Mac/Windows dev, or any host without
    # systemd), manage the process directly so gating still works everywhere.
    if shutil.which("systemctl") is not None:
        active = _service_is_active(config.systemd_unit)
        if should_gate and active:
            print(f"Gating: stopping {config.systemd_unit}")
            _systemctl("stop", config.systemd_unit)
        elif not should_gate and not active:
            print(f"Un-gating: starting {config.systemd_unit}")
            _systemctl("start", config.systemd_unit)
        return

    active = _daemon_is_running()
    if should_gate and active:
        print("Gating: stopping wake_word_daemon.py")
        _stop_daemon()
    elif not should_gate and not active:
        print("Un-gating: starting wake_word_daemon.py")
        _start_daemon()


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


def _speak_medication_digest(event, windows: list, out_device) -> None:
    """Dynamic TTS companion to the static entrance/exit WAV -- a medication
    schedule doesn't pause for Shabbat, but this device does, so this is the
    one chance to say it out loud: a heads-up right before candle-lighting
    for what's due *during* Shabbat/Yom Tov, and a catch-up right after
    havdalah for the same list, since none of it could actually be spoken
    while gated.

    Entirely best-effort: any failure here (calendar API, network, TTS) is
    swallowed so it can never affect the gate's own fail-closed enforcement
    or the static announcement that already played moments before this is
    called (see fire_due_announcements).
    """
    try:
        from brain import gcal
        from brain.respond import speak_reply

        if event.kind == "entrance":
            window = next((w for w in windows if w.start == event.at), None)
        elif event.kind == "exit":
            window = next((w for w in windows if w.end == event.at), None)
        else:
            return
        if window is None:
            return

        items = gcal.upcoming_between(window.start, window.end)
        if not items:
            return
        titles = list(dict.fromkeys(item.get("summary", "Reminder") for item in items))
        is_hebrew = any(any("֐" <= c <= "׾" for c in t) for t in titles)
        names = ", ".join(titles)

        if event.kind == "entrance":
            text = f"לפני שבת, תזכורת לתרופות: {names}" if is_hebrew else f"Before Shabbat, a medication reminder: {names}"
        else:
            text = f"תזכורות לתרופות מזמן השבת: {names}" if is_hebrew else f"Medication reminders from during Shabbat: {names}"

        speak_reply(text, out_device)
    except Exception as exc:
        print(f"Medication digest failed (non-fatal): {exc}", file=sys.stderr)


def fire_due_announcements(config: ShabbatConfig, events, now: datetime, windows: list) -> None:
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
        if event.kind in ("entrance", "exit"):
            _speak_medication_digest(event, windows, out_device)
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
    fire_due_announcements(config, events, now, windows)


if __name__ == "__main__":
    main()
