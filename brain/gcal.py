"""Google Calendar client for the "Mendy" reminders calendar.

Controls a dedicated secondary Google Calendar via a service account -- no
interactive OAuth, ever (see MENDY_CALENDAR_ID / GOOGLE_SERVICE_ACCOUNT_FILE
in brain/config.py for why that matters on an unattended Pi). The service
account only ever sees calendars explicitly shared with it, so it can't touch
anything else on the household's Google account.

One-time setup:
  1. Create a Google Cloud project, enable the Calendar API, create a service
     account, download its JSON key.
  2. In Google Calendar's web UI, create a new secondary calendar named
     "Mendy". Under that calendar's Settings -> "Share with specific people",
     add the service account's email (from the JSON key file) with
     "Make changes to events" permission.
  3. Copy that calendar's id (Settings -> "Integrate calendar" -> Calendar ID,
     looks like an email address) into .pi-config as MENDY_CALENDAR_ID. Put
     the JSON key's path in .env as GOOGLE_SERVICE_ACCOUNT_FILE.
  4. Run `uv run python -m brain.gcal` once to verify access.

Lazy import of the google client libraries inside _get_service(), same reason
brain/spotify.py imports spotipy lazily: importing this module (done at
startup by brain/tools.py) must never require the dependency or valid
credentials until a calendar tool is actually invoked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .config import GOOGLE_SERVICE_ACCOUNT_FILE, HOUSEHOLD_TIMEZONE, MENDY_CALENDAR_ID

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_CREATED_BY = "mendy-assistant"
_DEFAULT_EVENT_MINUTES = 15  # events are reminders, not meetings -- duration is cosmetic

_service = None


class CalendarError(Exception):
    pass


def _tz() -> ZoneInfo:
    return ZoneInfo(HOUSEHOLD_TIMEZONE)


def _get_service():
    """Lazily build an authenticated Calendar API client, cached across calls.

    Raises CalendarError (never blocks on any interactive flow) if the
    service account file or calendar id aren't configured yet -- mirrors
    brain/spotify.py's _get_client() contract exactly.
    """
    global _service
    if _service is not None:
        return _service

    if not GOOGLE_SERVICE_ACCOUNT_FILE:
        raise CalendarError(
            "Google Calendar isn't set up -- add GOOGLE_SERVICE_ACCOUNT_FILE to .env "
            "(path to a service account JSON key) and MENDY_CALENDAR_ID to .pi-config, "
            "see brain/gcal.py's module docstring for the one-time setup"
        )
    if not MENDY_CALENDAR_ID:
        raise CalendarError(
            "MENDY_CALENDAR_ID isn't set in .pi-config -- see brain/gcal.py's module "
            "docstring for how to create and share the calendar"
        )

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise CalendarError("google-api-python-client not installed -- run `uv sync`") from exc

    try:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES
        )
    except (OSError, ValueError) as exc:
        raise CalendarError(f"Couldn't load the service account key: {exc}") from exc

    _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def _localize(date: str, time_str: str) -> datetime:
    return datetime.fromisoformat(f"{date}T{time_str}:00").replace(tzinfo=_tz())


def _build_rrule(recurrence: str, count: int | None, until_date: str | None) -> str | None:
    if recurrence not in ("daily", "weekly"):
        return None
    freq = "DAILY" if recurrence == "daily" else "WEEKLY"
    rrule = f"RRULE:FREQ={freq}"
    if count:
        rrule += f";COUNT={count}"
    elif until_date:
        # UNTIL must be a UTC date-time when DTSTART is a date-time (RFC 5545)
        # -- end-of-day in household-local time, converted to UTC.
        until_local = datetime.fromisoformat(f"{until_date}T23:59:59").replace(tzinfo=_tz())
        rrule += f";UNTIL={until_local.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    return rrule


def _event_group(item: dict) -> str:
    return item.get("extendedProperties", {}).get("private", {}).get("reminder_group") or item["id"]


def _instance_label(item: dict) -> str:
    start = item.get("start", {})
    return start.get("dateTime") or start.get("date") or "?"


def add_event(
    title: str,
    date: str,
    times: list[str],
    recurrence: str = "none",
    count: int | None = None,
    until_date: str | None = None,
    notes: str = "",
) -> str:
    """Creates one Google Calendar event per entry in `times` -- Google has no
    sub-daily recurrence, so "8am and 8pm every day" needs two separate daily
    events, not one. All events from a single call share a short
    `reminder_group` id (stamped into extendedProperties.private) so they can
    later be listed/cancelled together as one logical reminder. Returns a
    short status string for Claude to turn into a spoken confirmation.
    """
    service = _get_service()
    if not times:
        return "status: error_no_times"

    group_id = uuid.uuid4().hex[:8]
    rrule = _build_rrule(recurrence, count, until_date)

    created_times = []
    try:
        for t in times:
            start = _localize(date, t)
            end = start + timedelta(minutes=_DEFAULT_EVENT_MINUTES)
            body: dict[str, Any] = {
                "summary": title,
                "description": notes,
                "start": {"dateTime": start.isoformat(), "timeZone": HOUSEHOLD_TIMEZONE},
                "end": {"dateTime": end.isoformat(), "timeZone": HOUSEHOLD_TIMEZONE},
                "extendedProperties": {
                    "private": {"reminder_group": group_id, "created_by": _CREATED_BY}
                },
            }
            if rrule:
                body["recurrence"] = [rrule]
            service.events().insert(calendarId=MENDY_CALENDAR_ID, body=body).execute()
            created_times.append(t)
    except Exception as exc:
        raise CalendarError(f"Couldn't create the event: {exc}") from exc

    repeats = f"repeats: {recurrence}" if recurrence != "none" else "repeats: once"
    if count:
        ends = f", ends after {count} occurrences"
    elif until_date:
        ends = f", ends {until_date}"
    else:
        ends = ""
    return (
        f"status: created, title: {title}, times: {' and '.join(created_times)}, "
        f"{repeats}{ends}, event_group: {group_id}"
    )


def list_events(days_ahead: int = 7) -> str:
    """Compact plain-text listing of upcoming event instances over the next
    `days_ahead` days, grouped by reminder_group so a twice-daily medication
    reads as one line, not two. Google expands recurrences itself
    (singleEvents=True) -- no local RRULE math.
    """
    service = _get_service()
    now = datetime.now(_tz())
    time_max = now + timedelta(days=days_ahead)
    try:
        result = (
            service.events()
            .list(
                calendarId=MENDY_CALENDAR_ID,
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as exc:
        raise CalendarError(f"Couldn't list events: {exc}") from exc

    items = result.get("items", [])
    if not items:
        return "status: empty, no upcoming events"

    order: list[str] = []
    groups: dict[str, dict] = {}
    for item in items:
        gid = _event_group(item)
        if gid not in groups:
            groups[gid] = {"title": item.get("summary", "Untitled"), "times": []}
            order.append(gid)
        groups[gid]["times"].append(_instance_label(item))

    lines = [f"{groups[gid]['title']} ({gid}): {', '.join(groups[gid]['times'])}" for gid in order]
    return "status: ok, upcoming: " + " | ".join(lines)


def cancel_events(query: str = "", event_group: str = "") -> str:
    """Deletes every upcoming event matching `event_group` exactly, or whose
    title contains `query` (case-insensitive substring -- mirrors
    memory.forget()'s match semantics: a person reviewing the tool result is
    the real safeguard against removing the wrong thing). Deleting a
    recurring series master removes the whole series, not just one instance.
    """
    service = _get_service()
    query = (query or "").strip().lower()
    event_group = (event_group or "").strip()
    if not query and not event_group:
        return "status: error_no_query"

    now = datetime.now(_tz())
    try:
        # singleEvents=False: list series masters (one per recurring group,
        # not one per expanded instance) so deleting removes the whole series.
        result = (
            service.events()
            .list(calendarId=MENDY_CALENDAR_ID, timeMin=now.isoformat(), singleEvents=False, maxResults=250)
            .execute()
        )
    except Exception as exc:
        raise CalendarError(f"Couldn't look up events to cancel: {exc}") from exc

    removed = []
    for item in result.get("items", []):
        title = item.get("summary", "")
        matches = (event_group and _event_group(item) == event_group) or (query and query in title.lower())
        if not matches:
            continue
        try:
            service.events().delete(calendarId=MENDY_CALENDAR_ID, eventId=item["id"]).execute()
            removed.append(f"{title} ({_instance_label(item)})")
        except Exception:
            continue

    if not removed:
        return "status: error_not_found"
    return f"status: cancelled, removed: {'; '.join(removed)}"


def upcoming_between(start: datetime, end: datetime) -> list[dict]:
    """Raw expanded event instances between `start` and `end` (both
    timezone-aware) -- used by brain/reminders.py's poller and
    shabbat/gate.py's medication digest, not by Claude directly.
    """
    service = _get_service()
    try:
        result = (
            service.events()
            .list(
                calendarId=MENDY_CALENDAR_ID,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as exc:
        raise CalendarError(f"Couldn't fetch events: {exc}") from exc
    return result.get("items", [])


if __name__ == "__main__":
    # One-time setup verification -- confirms the service account can see the
    # "Mendy" calendar and lists what's on it:
    #   uv run python -m brain.gcal
    _service = _get_service()
    _cal = _service.calendars().get(calendarId=MENDY_CALENDAR_ID).execute()
    print(f"Authorized. Connected to calendar: {_cal.get('summary')}")
    print(list_events(days_ahead=14))
