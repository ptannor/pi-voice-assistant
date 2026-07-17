"""Proactive spoken reminders for events on the "Mendy" Google Calendar.

A background thread (started by wake_word_daemon.main() via start(), see its
docstring) that polls upcoming calendar events and speaks each one aloud,
unprompted, at its start time -- the calendar analogue of brain/timer.py's
alarm, reusing the same ALERT audio-focus channel so it correctly
pauses/resumes music and preempts an in-progress spoken reply. Must run
inside the voice daemon's own process: brain/audio_focus.py's focus manager
is explicitly single-process, so only a thread here can ever acquire ALERT.

Two-tier polling: a slow FETCH_INTERVAL_SECONDS refresh from the Google API
(cheap on quota, and picks up edits made via the Calendar app or the Telegram
bot within one cycle) feeds an in-memory cache; a fast FIRE_INTERVAL_SECONDS
scan of that cache fires anything due now. Fired instances are recorded in a
small gitignored JSON file (keyed by Google's own per-instance event id) so a
daemon restart never re-announces something already spoken -- mirrors
shabbat/gate.py's _load_fired_ids/_save_fired_ids state-file pattern.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from audio_check.devices import Device
from audio_check.player import play_wav

from . import gcal
from .audio_focus import Channel, manager as focus
from .config import HOUSEHOLD_TIMEZONE, REMINDER_LEAD_MINUTES, TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
from .respond import speak_reply

FETCH_INTERVAL_SECONDS = 5 * 60
FIRE_INTERVAL_SECONDS = 20
FETCH_WINDOW_HOURS = 12
# Cap on waiting out an in-progress conversation before speaking the
# reminder anyway -- a ringing alarm the user set is allowed to preempt a
# reply outright (see audio_focus.py's ALERT > DIALOG ordering), but a
# medication reminder shouldn't hard-cut someone's in-flight answer for
# something that can wait a few seconds either.
DIALOG_DEFER_SECONDS = 90
# A reminder found this late (daemon/Pi was off right at its start time)
# still speaks; older than this, it's text-only -- announcing "take your 8am
# antibiotics" out loud at 2pm is more confusing than useful.
LATE_FIRE_GRACE_MINUTES = 30

STATE_PATH = Path(__file__).parent.parent / "logs" / "reminders_fired.json"
CHIME_WAV = Path(__file__).parent.parent / "assets" / "chime.wav"

_lead = timedelta(minutes=REMINDER_LEAD_MINUTES)


def _tz() -> ZoneInfo:
    return ZoneInfo(HOUSEHOLD_TIMEZONE)


def _load_fired() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        return set(json.loads(STATE_PATH.read_text()).get("fired", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_fired(fired: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"fired": sorted(fired)}))


def _instance_start(item: dict) -> datetime | None:
    raw = item.get("start", {}).get("dateTime")
    if not raw:
        return None  # all-day events aren't timed reminders
    return datetime.fromisoformat(raw)


def _is_hebrew(text: str) -> bool:
    return any("֐" <= c <= "׾" for c in text)


def _push_telegram(text: str) -> None:
    """Best-effort text push to every allowlisted chat. A Telegram outage (or
    the bot not being configured at all) must never block the spoken
    reminder, hence the broad except -- this is a nice-to-have companion to
    the speech, not the primary delivery mechanism.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALLOWED_CHAT_IDS:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_ALLOWED_CHAT_IDS:
        try:
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
            urllib.request.urlopen(url, data=data, timeout=5)
        except Exception:
            pass


def _speak(text: str, out_device: Device) -> None:
    focus.acquire(Channel.ALERT)
    try:
        try:
            play_wav(CHIME_WAV, out_device)
        except Exception:
            pass
        speak_reply(text, out_device)
    finally:
        focus.release(Channel.ALERT)


def _fire(item: dict, out_device: Device) -> None:
    title = item.get("summary", "Reminder")
    text = f"תזכורת: {title}" if _is_hebrew(title) else f"Reminder: {title}"
    print(f"Reminder firing: {title}", flush=True)
    try:
        _speak(text, out_device)
    except Exception as exc:
        print(f"Failed to speak reminder: {exc}", flush=True)
    _push_telegram(text)


def _fire_late(item: dict) -> None:
    # Too late to speak meaningfully (the Pi/daemon was off) -- still worth
    # letting the family know, just not out loud at a confusing time.
    title = item.get("summary", "Reminder")
    start = _instance_start(item)
    when = start.strftime("%H:%M") if start else "?"
    _push_telegram(f"(missed) {title} was due at {when}")


def _poll_loop(out_device: Device) -> None:
    fired = _load_fired()
    cache: list[dict] = []
    last_fetch = 0.0

    while True:
        now_monotonic = time.monotonic()
        if now_monotonic - last_fetch >= FETCH_INTERVAL_SECONDS or not cache:
            try:
                now = datetime.now(_tz())
                cache = gcal.upcoming_between(now, now + timedelta(hours=FETCH_WINDOW_HOURS))
            except Exception as exc:
                print(f"Reminder fetch failed: {exc}", flush=True)
            last_fetch = now_monotonic

        now = datetime.now(_tz())
        for item in cache:
            item_id = item.get("id")
            if not item_id or item_id in fired:
                continue
            start = _instance_start(item)
            if start is None:
                continue
            due_at = start - _lead
            if due_at > now:
                continue

            late_by = now - due_at
            fired.add(item_id)
            _save_fired(fired)

            if late_by > timedelta(minutes=LATE_FIRE_GRACE_MINUTES):
                _fire_late(item)
                continue

            deferred_until = time.monotonic() + DIALOG_DEFER_SECONDS
            while focus.is_active(Channel.DIALOG) and time.monotonic() < deferred_until:
                time.sleep(2)
            _fire(item, out_device)

        time.sleep(FIRE_INTERVAL_SECONDS)


def start(out_device: Device) -> None:
    """Starts the reminder poller as a daemon thread.

    Best-effort by design: a missing/misconfigured calendar (brain/gcal.py
    not set up yet) must not crash the voice daemon -- reminders just won't
    fire until it is, the same tolerance brain/spotify.py extends to a
    not-yet-authorized Spotify account. Failures inside the loop are caught
    and logged per-iteration (see _poll_loop), not here.
    """
    threading.Thread(target=_poll_loop, args=(out_device,), daemon=True).start()
