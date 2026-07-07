"""Web search via Serper.dev (see config.py for why), with a rolling 7-day
cache per query since it's not free forever -- 2,500 queries goes a long way,
and a household re-asking the same kind of question over a week shouldn't
burn a fresh query each time. Trade-off: genuinely time-sensitive queries
(e.g. today's movie showtimes) can return a stale week-old answer if asked
again a few days later -- fine for this project's actual usage so far, but
worth revisiting if that turns out to matter in practice.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from .config import SERPER_API_KEY

SERPER_URL = "https://google.serper.dev/search"
CACHE_PATH = Path(__file__).parent.parent / ".web_search_cache.json"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


class WebSearchError(Exception):
    pass


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass  # caching is a cost optimization, not correctness-critical -- don't fail the search over it


def _normalize(query: str) -> str:
    return " ".join(query.lower().split())


def search(query: str) -> str:
    """Return a short text summary of top web results for `query`, formatted
    for Claude to synthesize an answer from -- not the answer itself.
    """
    if not SERPER_API_KEY:
        raise WebSearchError("SERPER_API_KEY not set -- add it to .env")

    key = _normalize(query)
    cache = _load_cache()
    cached = cache.get(key)
    if cached and time.time() - cached["timestamp"] < CACHE_TTL_SECONDS:
        return cached["result"]

    request = urllib.request.Request(
        SERPER_URL,
        data=json.dumps({"q": query}).encode("utf-8"),
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise WebSearchError(f"Serper request failed: {exc}") from exc

    organic = data.get("organic", [])[:5]
    if not organic:
        result = "No web results found."
    else:
        result = "\n".join(f"- {r.get('title', '')}: {r.get('snippet', '')}" for r in organic)

    cache[key] = {"result": result, "timestamp": time.time()}
    _save_cache(cache)
    return result
