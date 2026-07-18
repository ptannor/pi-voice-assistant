"""Today's daily halacha (Jewish law) teaching.

Two ways to deliver it, tried in this order:

1. **A real short recording** (pick_short_halacha_episode) -- searches the
   household's existing Spotify catalog access for actual short (real rabbi,
   real audio) halacha episodes, e.g. the "דקה של הלכה" ("A minute of
   halacha") series, and plays one via brain/spotify.py's existing
   play()/search infrastructure -- reusing the exact same Spotify plumbing
   the music-playback tools already use, not a new audio pipeline. Much
   better listening experience than TTS reading composed text aloud.
   Tracks which episode ids have already been played (logs/ file, mirrors
   shabbat/gate.py's fired-id state pattern) so the same clip doesn't repeat
   while there's still a fresh one available -- once the visible pool is
   exhausted, repeats are allowed rather than failing (real recordings are
   worth more than a repeat, and worth much more than a TTS-read one -- see
   _MAX_AUDIO_SECONDS below for why the pool is sized to make repeats rare
   in the first place rather than leaning on this fallback).
2. **TTS-composed fallback** (get_daily_halacha_text) -- only reached if no
   suitable recording turns up at all (Spotify not configured, network
   hiccup, nothing short enough found). Sourced via a real web search each
   day --
   Claude's own training data isn't a reliable source for exact, current
   halachic rulings (same reason brain/websearch.py exists at all for
   time-sensitive/factual questions). Composed into one concise, TTS-ready
   teaching by a single, non-conversational Claude call (no tools, no
   history -- this isn't part of the interactive brain/llm.py ask() loop,
   since brain/reminders.py's proactive 4pm calendar reminder needs the same
   text with no conversation to run it in). The date is folded directly into
   the search query (not a separate cache layer here) so
   brain/websearch.py's own per-query cache naturally keys one result per
   calendar day.
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, HOUSEHOLD_TIMEZONE
from .websearch import WebSearchError, search

_QUERY_HE = "הלכה יומית"
_QUERY_EN = "daily halacha jewish law today"

# Real (rabbi-recorded, not TTS) halacha episodes on Spotify -- extra search
# angles to widen the pool, filtered by _MAX_AUDIO_SECONDS below regardless
# of how they're titled. "הלכה יומית" (bare, no "קצר"/"short" qualifier) is
# the one that actually matters most: confirmed live it's a real, actively-
# produced, dated daily halacha series (episode titles carry the Hebrew
# date) with dozens of episodes in the ~2-4 minute range -- adding it
# unqualified (rather than only searching for it combined with "short")
# is what actually surfaces that pool; the "short"-qualified variants alone
# only ever turned up a handful of literal one-minute clips.
_AUDIO_SEARCH_QUERIES = (
    "דקה של הלכה", "הלכה בקצרה", "הלכה יומית קצר", "הלכה יומית", "הלכה ליום",
)
# Confirmed live: capping at 75s (only true "one-minute" format clips) left
# a pool of just 3-5 distinct recordings, some of them literal duplicate
# uploads of the same clip -- exhausted almost immediately with regular use
# and started repeating. 300s (5 min) is still a reasonable spoken-teaching
# length and unlocks ~25 distinct real episodes from the "הלכה יומית" series
# alone, which is the actual lever that matters here, not the exact number.
_MAX_AUDIO_SECONDS = 300
_PLAYED_STATE_PATH = Path(__file__).parent.parent / "logs" / "halacha_audio_played.json"

_SYSTEM_HE = (
    "אתה מכין הלכה יומית קצרה שתיקרא בקול, מתוך תוצאות חיפוש אמיתיות למטה. "
    "כתוב 2-3 משפטים קצרים וברורים בעברית פשוטה -- בלי רשימות, בלי הדגשות "
    "(כמו כוכביות), בלי קישורים, כי זה נקרא בקול על ידי מנוע קול ולא מוצג "
    "כטקסט. אם התוצאות לא ברורות או לא רלוונטיות, בחר מתוכן את ההלכה הכי "
    "ברורה והכי שימושית ליום-יום."
)
_SYSTEM_EN = (
    "Prepare a short daily halacha (Jewish law) teaching to be read aloud, "
    "based on the real search results below. Write 2-3 short, clear "
    "sentences in plain text -- no lists, no bold/markdown, no links, since "
    "this is read aloud by a voice engine, not shown as text. If the "
    "results are unclear or off-topic, pick whichever halacha among them is "
    "clearest and most useful day-to-day."
)

# Same defensive stripping rationale as brain/llm.py's own TTS post-processing
# (a system-prompt instruction alone isn't reliably followed) -- kept as a
# small local copy rather than importing brain.llm, which would create a
# circular import (llm -> tools -> halacha -> llm).
_URL_RE = re.compile(r"https?://\S+")
_MARKDOWN_RE = re.compile(r"[*_`#]")

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _today_str() -> str:
    return datetime.now(ZoneInfo(HOUSEHOLD_TIMEZONE)).strftime("%Y-%m-%d")


def _load_played() -> set[str]:
    if not _PLAYED_STATE_PATH.exists():
        return set()
    try:
        return set(json.loads(_PLAYED_STATE_PATH.read_text()).get("played", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_played(played: set[str]) -> None:
    try:
        _PLAYED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLAYED_STATE_PATH.write_text(json.dumps({"played": sorted(played)}))
    except OSError:
        pass  # best-effort -- worst case a clip repeats sooner than ideal


def pick_short_halacha_episode() -> dict | None:
    """Search the household's Spotify catalog access for a short, real
    halacha recording and return {"uri", "name", "duration_s"} for the
    caller to play via brain/spotify.py's play() -- or None if nothing
    suitable turns up (caller falls back to get_daily_halacha_text)."""
    from . import spotify

    try:
        sp = spotify._get_client()
    except spotify.SpotifyError:
        return None

    candidates: dict[str, dict] = {}
    for query in _AUDIO_SEARCH_QUERIES:
        try:
            results = sp.search(q=query, type="episode", limit=10)
        except Exception:
            continue
        for item in results.get("episodes", {}).get("items", []):
            duration_ms = item.get("duration_ms")
            if duration_ms and duration_ms / 1000 <= _MAX_AUDIO_SECONDS:
                candidates[item["id"]] = item

    if not candidates:
        return None

    played = _load_played()
    fresh = {k: v for k, v in candidates.items() if k not in played}
    pool = fresh or candidates  # pool exhausted -- allow a repeat rather than nothing

    chosen_id = random.choice(list(pool.keys()))
    chosen = pool[chosen_id]

    played.add(chosen_id)
    _save_played(played)

    return {
        "uri": chosen["uri"],
        "name": chosen["name"],
        "duration_s": round(chosen["duration_ms"] / 1000),
    }


def _clean(text: str) -> str:
    text = _URL_RE.sub("", text)
    text = _MARKDOWN_RE.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def get_daily_halacha_text(language: str = "he") -> str:
    """Returns a short status string, same convention as brain/spotify.py:
    "status: ok, text: ..." on success, "status: error_*" otherwise."""
    if not ANTHROPIC_API_KEY:
        return "status: error_not_configured"

    query = f"{_QUERY_HE if language == 'he' else _QUERY_EN} {_today_str()}"
    try:
        raw = search(query)
    except WebSearchError as exc:
        return f"status: error_search_failed, details: {exc}"

    try:
        response = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            system=_SYSTEM_HE if language == "he" else _SYSTEM_EN,
            messages=[{"role": "user", "content": raw}],
        )
    except Exception as exc:
        return f"status: error_compose_failed, details: {exc}"

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        return "status: error_compose_failed"
    return f"status: ok, text: {_clean(text)}"
