from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class GateWindow:
    start: datetime  # candle-lighting time
    end: datetime  # havdalah time
    is_yomtov: bool  # True if a full Yom Tov day (not just a weekly Shabbat) falls in this window


@dataclass(frozen=True)
class ScheduledEvent:
    at: datetime
    kind: str  # "warning" | "entrance" | "exit"
    is_yomtov: bool
    minutes_before: int | None = None  # only set for "warning"

    @property
    def event_id(self) -> str:
        suffix = f"_{self.minutes_before}" if self.minutes_before is not None else ""
        return f"{self.at.isoformat()}_{self.kind}{suffix}"


def _parse_dt(item: dict) -> datetime:
    return datetime.fromisoformat(item["date"])


def build_windows(items: list[dict]) -> list[GateWindow]:
    """Merge candle-lighting/havdalah events into gate windows.

    Multi-day Yom Tov (e.g. two days of Rosh Hashana) produces a second
    "candles" event before the final "havdalah" -- that interior candle
    lighting doesn't close and reopen the gate, it's a continuation of the
    same window, so it's absorbed rather than treated as a separate window.
    """
    yomtov_dates = {item["date"] for item in items if item.get("yomtov")}

    boundary_events = sorted(
        (
            (_parse_dt(item), item["category"])
            for item in items
            if item.get("category") in ("candles", "havdalah")
        ),
        key=lambda pair: pair[0],
    )

    windows: list[GateWindow] = []
    window_start: datetime | None = None
    window_has_yomtov = False

    for dt, category in boundary_events:
        if category == "candles":
            if window_start is None:
                window_start = dt
            # A yomtov day whose evening starts here (the day *following* this
            # candle-lighting) marks the window as a Yom Tov window.
            next_day = (dt + timedelta(days=1)).date().isoformat()
            if any(d.startswith(next_day) for d in yomtov_dates):
                window_has_yomtov = True
        elif category == "havdalah" and window_start is not None:
            windows.append(GateWindow(start=window_start, end=dt, is_yomtov=window_has_yomtov))
            window_start = None
            window_has_yomtov = False

    return windows


def scheduled_events(
    windows: list[GateWindow], warning_offsets_minutes: tuple[int, ...]
) -> list[ScheduledEvent]:
    events: list[ScheduledEvent] = []
    for w in windows:
        for minutes in warning_offsets_minutes:
            events.append(
                ScheduledEvent(
                    at=w.start - timedelta(minutes=minutes),
                    kind="warning",
                    is_yomtov=w.is_yomtov,
                    minutes_before=minutes,
                )
            )
        events.append(ScheduledEvent(at=w.start, kind="entrance", is_yomtov=w.is_yomtov))
        events.append(ScheduledEvent(at=w.end, kind="exit", is_yomtov=w.is_yomtov))
    return events


def is_gated(windows: list[GateWindow], now: datetime) -> bool:
    return any(w.start <= now < w.end for w in windows)


_HE_WEEKDAYS = ("יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת", "יום ראשון")
_EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def concise_times_text(windows: list[GateWindow], now: datetime, language: str = "he") -> str:
    """Short, TTS-ready candle-lighting/havdalah times for the current (if
    `now` falls inside one) or next upcoming window -- same Shabbat/Yom Tov
    wording distinction as the gate's own spoken announcements (see
    docs/specs/shabbat-gating.md's Timeline), so this reads the same way
    Mendy already talks about Shabbat elsewhere. Returns a short status
    string, same convention as brain/spotify.py.
    """
    upcoming = next((w for w in windows if w.end > now), None)
    if upcoming is None:
        return "status: error_not_found"

    start_time = upcoming.start.strftime("%H:%M")
    end_time = upcoming.end.strftime("%H:%M")

    if language == "he":
        occasion = "החג" if upcoming.is_yomtov else "השבת"
        day = _HE_WEEKDAYS[upcoming.start.weekday()]
        text = f"כניסת {occasion} ב{day} בשעה {start_time}, צאת {occasion} בשעה {end_time}"
    else:
        occasion = "the holiday" if upcoming.is_yomtov else "Shabbat"
        day = _EN_WEEKDAYS[upcoming.start.weekday()]
        text = f"{occasion} begins {day} at {start_time} and ends at {end_time}"

    return f"status: ok, text: {text}"
