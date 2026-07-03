from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import ShabbatConfig

HEBCAL_URL = "https://www.hebcal.com/hebcal"


class ShabbatDataError(Exception):
    pass


def _fetch_year(config: ShabbatConfig, year: int) -> list[dict]:
    if not config.geonameid:
        raise ShabbatDataError(
            "SHABBAT_GEONAMEID not set in .pi-config -- see README for how to configure "
            "your location for zmanim lookups."
        )
    params = {
        "v": "1",
        "cfg": "json",
        "geonameid": config.geonameid,
        "year": str(year),
        "maj": "on",
        "min": "on",
        "mod": "on",
        "c": "on",
        "b": str(config.candle_lighting_offset_minutes),
        "M": "on",
        "td": str(config.havdalah_degrees),
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{HEBCAL_URL}?{query}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("items", [])


def fetch_and_cache(config: ShabbatConfig) -> dict:
    """Fetch this year and next year's data (so we never run out near year-end) and cache it."""
    now = datetime.now(timezone.utc)
    items = _fetch_year(config, now.year) + _fetch_year(config, now.year + 1)
    cache = {"fetched_at": now.isoformat(), "items": items}
    config.cache_path.write_text(json.dumps(cache))
    return cache


def load_cache(config: ShabbatConfig) -> dict | None:
    if not config.cache_path.exists():
        return None
    try:
        return json.loads(config.cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def cache_age_days(cache: dict) -> float:
    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    return (datetime.now(timezone.utc) - fetched_at).total_seconds() / 86400


def get_data(config: ShabbatConfig) -> tuple[list[dict] | None, str]:
    """Returns (items, status) where status is one of:
    'fresh' (used cache, no refresh needed), 'refreshed' (fetched new data),
    'stale_ok' (refresh failed but cache still under max age, used anyway),
    'untrusted' (no usable data -- caller must fail closed).
    """
    cache = load_cache(config)

    if cache is not None:
        age = cache_age_days(cache)
        if age < config.cache_refresh_days:
            return cache["items"], "fresh"
    else:
        age = None

    try:
        fresh_cache = fetch_and_cache(config)
        return fresh_cache["items"], "refreshed"
    except (ShabbatDataError, OSError, ValueError) as exc:
        if cache is not None and age is not None and age <= config.cache_max_age_days:
            print(f"Warning: refresh failed ({exc}), using cache ({age:.1f} days old)")
            return cache["items"], "stale_ok"
        return None, "untrusted"
