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

Every reminder carries a critical/morning/regular/uncertain category (see
brain/classify.py) stamped onto the calendar event itself, either at creation
(brain/tools.py's add_calendar_event) or by _reclassify_new_items below for
anything created/edited outside Mendy -- the Calendar app, or directly via
Telegram. A second background thread, _critical_nudge_loop, separately
handles the "keep checking in until acknowledged" behavior critical items
need, on its own slower cadence -- see its docstring.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from audio_check.devices import Device
from audio_check.player import play_wav

from . import gcal
from .audio_focus import Channel, manager as focus
from .config import (
    CRITICAL_REMINDER_SOUND_PATH,
    HOUSEHOLD_TIMEZONE,
    REMINDER_LEAD_MINUTES,
    REMINDER_SOUND_PATH,
    WAKEUP_SOUND_PATH,
    WAKEUP_TITLE_KEYWORD,
)
from .respond import speak_reply
from .telegram_push import push as push_telegram

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

# How often a still-unhandled critical reminder gets checked in on again --
# "a few times a day", not a tight repeat -- and the local-hour window that's
# allowed to happen in, so nobody gets proactively nudged awake at 3am about
# a flight. Applies both to the proactive spoken check-in (_critical_nudge_loop)
# and to the conversational mention (brain/llm.py's _critical_reminders_prompt_line).
CRITICAL_NUDGE_INTERVAL_HOURS = 3
CRITICAL_QUIET_HOURS = (8, 21)  # [start, end) local hour, 24h clock

STATE_PATH = Path(__file__).parent.parent / "logs" / "reminders_fired.json"
CRITICAL_STATE_PATH = Path(__file__).parent.parent / "logs" / "critical_pending.json"
CHIME_WAV = Path(__file__).parent.parent / "assets" / "chime.wav"

# Matches the recurring "הלכה יומית" calendar event (see brain/halacha.py's
# module docstring and the README's Daily halacha section for the one-time
# `add_calendar_event` setup) -- when a reminder with this in its title
# fires, speak a real teaching instead of just announcing the event's own
# title like any other reminder.
HALACHA_TITLE_KEYWORD = "הלכה"

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


def _sound_for(category: str | None, title: str) -> Path:
    """Which sound to play before speaking a reminder titled `title` in
    category `category` (critical/morning/regular/uncertain/None -- see
    brain/gcal.py's category_of). WAKEUP_TITLE_KEYWORD is kept as a manual
    fallback alongside the "morning" category (not replaced by it) so a
    title-based override still works even before the reclassification poll
    or for a household that never uses the classifier's morning category by
    that exact name. Falls back to the generic chime if the relevant path
    isn't configured (see brain/config.py, local_sounds/ isn't populated in
    a fresh clone).
    """
    if category == "morning" or (WAKEUP_TITLE_KEYWORD and WAKEUP_TITLE_KEYWORD.lower() in title.lower()):
        return Path(WAKEUP_SOUND_PATH) if WAKEUP_SOUND_PATH else CHIME_WAV
    if category == "critical":
        return Path(CRITICAL_REMINDER_SOUND_PATH) if CRITICAL_REMINDER_SOUND_PATH else CHIME_WAV
    return Path(REMINDER_SOUND_PATH) if REMINDER_SOUND_PATH else CHIME_WAV


def _speak(text: str, category: str | None, title: str, out_device: Device) -> None:
    focus.acquire(Channel.ALERT)
    try:
        try:
            play_wav(_sound_for(category, title), out_device)
        except Exception:
            pass
        speak_reply(text, out_device)
    finally:
        focus.release(Channel.ALERT)


# -- Critical-reminder pending state -----------------------------------------
# Persisted (survives a daemon restart) rather than in-memory, since "nudge a
# few times a day until acknowledged" needs to keep working across restarts,
# not just within one process lifetime like the old fixed-nag-thread design
# did. Keyed by the reminder's Google Calendar event id.


def _load_critical_state() -> dict:
    if not CRITICAL_STATE_PATH.exists():
        return {}
    try:
        return json.loads(CRITICAL_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_critical_state(state: dict) -> None:
    CRITICAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRITICAL_STATE_PATH.write_text(json.dumps(state))


def _register_critical(item_id: str, title: str) -> None:
    state = _load_critical_state()
    if item_id not in state:
        state[item_id] = {"title": title, "last_nudged_at": datetime.now().isoformat()}
        _save_critical_state(state)


def pending_critical_items() -> list[dict]:
    """[{"id", "title", "last_nudged_at"}] for every critical reminder still
    awaiting explicit acknowledgement (see acknowledge_critical) -- read by
    brain/llm.py to decide whether to naturally bring one up this turn, and
    by _critical_nudge_loop for the proactive spoken check-in."""
    return [{"id": item_id, **info} for item_id, info in _load_critical_state().items()]


def mark_critical_nudged(item_id: str) -> None:
    state = _load_critical_state()
    if item_id in state:
        state[item_id]["last_nudged_at"] = datetime.now().isoformat()
        _save_critical_state(state)


def acknowledge_critical(query: str) -> str:
    """Matches a pending critical reminder by title substring (case-
    insensitive) and clears it -- called by the acknowledge_reminder tool
    once a household member explicitly confirms it's handled, e.g. "I already
    booked the flight." Returns a status string for Claude to confirm from."""
    query = query.strip().lower()
    state = _load_critical_state()
    match_id = next((item_id for item_id, info in state.items() if query in info["title"].lower()), None)
    if match_id is None:
        return "status: error_not_found"
    title = state.pop(match_id)["title"]
    _save_critical_state(state)
    return f"status: acknowledged, title: {title}"


def _within_quiet_hours(now: datetime) -> bool:
    return CRITICAL_QUIET_HOURS[0] <= now.hour < CRITICAL_QUIET_HOURS[1]


def _critical_nudge_loop(out_device: Device) -> None:
    """Proactive spoken check-in for still-unacknowledged critical reminders
    -- the "a few times a day" half of the redesigned critical behavior; the
    conversational half lives in brain/llm.py's _critical_reminders_prompt_line,
    which shares this same last_nudged_at bookkeeping so a chat mention and a
    proactive spoken check-in don't both fire within the same interval.
    Skips entirely outside CRITICAL_QUIET_HOURS, and while a conversation or
    another alert is already using the ALERT/DIALOG channel -- a live chat is
    the more natural place for this anyway, see brain/llm.py.
    """
    while True:
        time.sleep(15 * 60)
        now = datetime.now(_tz())
        if not _within_quiet_hours(now):
            continue
        if focus.is_active(Channel.DIALOG) or focus.is_active(Channel.ALERT):
            continue
        for item in pending_critical_items():
            try:
                last_nudged = datetime.fromisoformat(item["last_nudged_at"])
            except ValueError:
                last_nudged = now
            if now - last_nudged < timedelta(hours=CRITICAL_NUDGE_INTERVAL_HOURS):
                continue
            title = item["title"]
            text = f"תזכורת -- טיפלת ב{title}?" if _is_hebrew(title) else f"Reminder -- have you taken care of {title}?"
            try:
                _speak(text, "critical", title, out_device)
            except Exception as exc:
                print(f"Critical nudge speak failed: {exc}", flush=True)
            mark_critical_nudged(item["id"])
            break  # one spoken check-in per wake-up, not a burst through the whole list


# -- Reclassification ---------------------------------------------------------


def _reclassify_new_items(cache: list[dict]) -> None:
    """Assigns a category to anything in `cache` that doesn't have one yet --
    catches reminders added or edited outside Mendy (the Calendar app
    directly, or a Telegram message not routed through add_calendar_event).
    Runs once per FETCH_INTERVAL_SECONDS refresh (the same ~5 minute cadence
    that already picks up those external edits at all), not every
    FIRE_INTERVAL_SECONDS scan -- classifying is an API call, no need to
    repeat it every 20 seconds against the same still-unfired cache.
    """
    from . import classify

    seen_groups: set[str] = set()
    for item in cache:
        group = gcal.event_group(item)
        if group in seen_groups:
            continue
        seen_groups.add(group)
        if gcal.category_of(item) is not None:
            continue  # already classified (incl. "uncertain" -- already queued)

        title = item.get("summary", "")
        notes = item.get("description", "") or ""
        category = classify.classify_reminder(title, notes)
        try:
            gcal.set_category_for_group(group, category)
        except gcal.CalendarError as exc:
            print(f"Reclassification failed for {title!r}: {exc}", flush=True)
            continue
        print(f"Reclassified {title!r} as {category}", flush=True)
        if category == classify.UNCERTAIN:
            classify.queue_uncertain(group, title)
            push_telegram(classify.uncertain_question_text(title))


def _halacha_text(title: str) -> str | None:
    """Real daily halacha teaching if `title` is the recurring halacha
    reminder (see HALACHA_TITLE_KEYWORD), else None to fall back to the
    generic "Reminder: {title}" line. Best-effort: a search/API failure
    falls back to the generic line too rather than raising -- this runs
    unattended in a background thread, not a conversation Claude can recover
    from."""
    if HALACHA_TITLE_KEYWORD not in title:
        return None
    from . import halacha

    result = halacha.get_daily_halacha_text("he")
    prefix = "status: ok, text: "
    if result.startswith(prefix):
        return result[len(prefix):]
    print(f"Daily halacha fetch failed ({result}), falling back to generic reminder text", flush=True)
    return None


def _speak_halacha_audio(episode: dict, out_device: Device) -> None:
    """Plays a real recorded halacha episode (see brain/halacha.py's
    pick_short_halacha_episode) instead of TTS. Holds ALERT for the
    episode's actual duration -- spotify.play() only starts playback, it
    doesn't block until the clip ends -- plus a short buffer, so a
    lower-priority sound can't sneak back in before it's actually done."""
    from . import spotify

    focus.acquire(Channel.ALERT)
    try:
        try:
            play_wav(CHIME_WAV, out_device)
        except Exception:
            pass
        spotify.play(episode["uri"])
        time.sleep(episode["duration_s"] + 2)
    finally:
        focus.release(Channel.ALERT)


def _fire(item: dict, out_device: Device) -> None:
    title = item.get("summary", "Reminder")
    item_id = item.get("id")
    category = gcal.category_of(item)

    if HALACHA_TITLE_KEYWORD in title:
        print(f"Daily halacha reminder firing: {title}", flush=True)
        from . import halacha

        episode = halacha.pick_short_halacha_episode()
        if episode:
            try:
                _speak_halacha_audio(episode, out_device)
                push_telegram(f"הלכה יומית: {episode['name']}")
                return
            except Exception as exc:
                print(f"Halacha audio playback failed ({exc}), falling back to TTS", flush=True)
        # No audio found, or playback failed -- fall through to the
        # TTS-composed teaching below, same as any other reminder.

    text = _halacha_text(title) or (f"תזכורת: {title}" if _is_hebrew(title) else f"Reminder: {title}")

    if item_id and category == "critical":
        print(f"Critical reminder firing: {title}", flush=True)
        _register_critical(item_id, title)
        try:
            _speak(text, category, title, out_device)
        except Exception as exc:
            print(f"Failed to speak critical reminder: {exc}", flush=True)
        push_telegram(text)
        return

    print(f"Reminder firing: {title}", flush=True)
    try:
        _speak(text, category, title, out_device)
    except Exception as exc:
        print(f"Failed to speak reminder: {exc}", flush=True)
    push_telegram(text)


def _fire_late(item: dict) -> None:
    # Too late to speak meaningfully (the Pi/daemon was off) -- still worth
    # letting the family know, just not out loud at a confusing time.
    title = item.get("summary", "Reminder")
    start = _instance_start(item)
    when = start.strftime("%H:%M") if start else "?"
    push_telegram(f"(missed) {title} was due at {when}")


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
                _reclassify_new_items(cache)
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
    """Starts the reminder poller and the critical-nudge checker as daemon
    threads.

    Best-effort by design: a missing/misconfigured calendar (brain/gcal.py
    not set up yet) must not crash the voice daemon -- reminders just won't
    fire until it is, the same tolerance brain/spotify.py extends to a
    not-yet-authorized Spotify account. Failures inside either loop are
    caught and logged per-iteration, not here.
    """
    threading.Thread(target=_poll_loop, args=(out_device,), daemon=True).start()
    threading.Thread(target=_critical_nudge_loop, args=(out_device,), daemon=True).start()
