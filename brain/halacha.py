"""Today's daily halacha (Jewish law) teaching.

Two ways to deliver it, tried in this order:

1. **A real short recording** (pick_short_halacha_episode) -- samples a
   random page from one of a few known, prolific real (rabbi-recorded, not
   TTS) daily halacha shows' own episode back-catalogs on Spotify (see
   _HALACHA_SHOW_IDS -- hundreds to low-thousands of episodes each), and
   plays one via brain/spotify.py's existing play() infrastructure --
   reusing the exact same Spotify plumbing the music-playback tools already
   use, not a new audio pipeline. Much better listening experience than TTS
   reading composed text aloud. Tracks a *play count* per episode id (logs/
   file, mirrors shabbat/gate.py's fired-id state pattern) rather than a
   bare played/not-played flag, and always prefers whichever count-tier is
   lowest among that call's sampled candidates -- every episode not yet
   seen at all is implicitly count 0, which stays preferred for as long as
   any genuinely fresh one is still findable, so no episode is ever picked
   a 2nd time while another sampled candidate hasn't had its 1st; once the
   whole pool available on a given call has cycled at least once, the same
   preference (lowest count first) keeps repeats spread evenly too, instead
   of one clip drifting ahead of the rest. See _MAX_AUDIO_SECONDS below for
   why the pool is sized to make repeats rare in the first place.
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

# Real (rabbi-recorded, not TTS), actively-produced daily/short halacha shows
# on Spotify -- id -> display name (for the comment/debugging only, not used
# at runtime). Found by searching type="show" for "הלכה יומית": each has
# hundreds to low-thousands of real episodes, most a few minutes long.
# Confirmed a keyword search over individual episodes (the old approach)
# only ever turns up a handful of results (Spotify's episode search caps at
# 10 per query and doesn't surface anywhere near a show's full catalog) --
# going straight to a show's own episode list is what actually unlocks its
# whole back-catalog. Hard-coded rather than re-searched by show name every
# call (that's an extra network round-trip; these ids are stable) -- update
# this list if a show is ever removed or a better one turns up.
_HALACHA_SHOW_IDS = (
    "6CzeeC3wATileSU3DjhNQk",  # הפנינה היומית - הלכה יומית מפניני הלכה, ~1476 episodes
    "6juflBDzUlyCj8WALMNrWM",  # הלכה יומית - פינת ההלכה לאור המשפט העברי, ~718 episodes
    "372wcgv3dxKLcqRfl7wsTq",  # הרה"ג אהרון בוטבול, ~744 episodes
)
_EPISODES_PER_PAGE = 50  # Spotify's max page size for a show's episode list
# Confirmed live: capping at 75s (only true "one-minute format" clips, back
# when sourced from individual keyword search) left a pool of just 3-5
# distinct recordings -- exhausted almost immediately with regular use and
# started repeating. 300s (5 min) is still a reasonable spoken-teaching
# length; sampling a random page from the show catalogs above, ~1 in 4
# episodes falls under it, so across their combined ~2900 total episodes
# that's still several hundred distinct candidates, not a handful.
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


def _load_counts() -> dict[str, int]:
    if not _PLAYED_STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(_PLAYED_STATE_PATH.read_text())
        if "counts" in raw:
            return {k: int(v) for k, v in raw["counts"].items()}
        # Back-compat: the older state file format was just a flat
        # "played" list (each entry implicitly played once) -- migrate that
        # history into counts instead of silently discarding it and
        # re-treating everything as brand new.
        return {episode_id: 1 for episode_id in raw.get("played", [])}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _save_counts(counts: dict[str, int]) -> None:
    try:
        _PLAYED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLAYED_STATE_PATH.write_text(json.dumps({"counts": counts}, sort_keys=True))
    except OSError:
        pass  # best-effort -- worst case a clip repeats sooner than ideal


def pick_short_halacha_episode() -> dict | None:
    """Sample a random page from one of _HALACHA_SHOW_IDS's real back-catalogs
    and return {"uri", "name", "duration_s", "play_count"} for the caller to
    play via brain/spotify.py's play() -- or None if nothing suitable turns
    up at all (caller falls back to get_daily_halacha_text).

    Shows are tried in random order; each is asked for one randomly-offset
    page of its own episode list (not a keyword search -- see
    _HALACHA_SHOW_IDS's comment for why that surfaces far more real
    candidates). Stops at the first show whose sampled page has something
    never played at all (count 0), so this is normally one Spotify
    round-trip (a show lookup for its episode count, plus one page fetch) --
    but keeps sampling the other (there are only a few) known shows if it
    doesn't, specifically to avoid settling for a repeat when a genuinely
    fresh episode might still be one page away. Whatever ends up in
    `candidates` after that, the choice is always made from whichever count
    tier is lowest among them, so an episode already played twice is never
    picked while one played once (or never) is right there in the same
    sample -- see _load_counts/_save_counts and the module docstring.
    """
    from . import spotify

    try:
        sp = spotify._get_client()
    except spotify.SpotifyError:
        return None

    counts = _load_counts()
    show_ids = list(_HALACHA_SHOW_IDS)
    random.shuffle(show_ids)

    candidates: dict[str, dict] = {}
    for show_id in show_ids:
        try:
            total = sp.show(show_id).get("total_episodes") or 0
            if total <= 0:
                continue
            offset = random.randint(0, max(0, total - _EPISODES_PER_PAGE))
            page = sp.show_episodes(show_id, limit=_EPISODES_PER_PAGE, offset=offset)
        except Exception:
            continue

        for item in page.get("items", []):
            duration_ms = item.get("duration_ms")
            if not (duration_ms and duration_ms / 1000 <= _MAX_AUDIO_SECONDS):
                continue
            # Defense in depth: these shows are Hebrew-only in practice
            # (confirmed live, both at show- and episode-level metadata),
            # but Torah content must always be Hebrew per policy (see
            # brain/llm.py) -- don't rely on that holding by chance forever.
            # Missing metadata (empty/absent "languages") doesn't reject,
            # since not everything Spotify serves populates it.
            languages = item.get("languages") or []
            if languages and "he" not in languages:
                continue
            candidates[item["id"]] = item

        if any(counts.get(k, 0) == 0 for k in candidates):
            break  # found something never played at all -- no need to sample another show

    if not candidates:
        return None

    min_count = min(counts.get(episode_id, 0) for episode_id in candidates)
    pool = {k: v for k, v in candidates.items() if counts.get(k, 0) == min_count}

    chosen_id = random.choice(list(pool.keys()))
    chosen = pool[chosen_id]

    counts[chosen_id] = counts.get(chosen_id, 0) + 1
    _save_counts(counts)

    return {
        "uri": chosen["uri"],
        "name": chosen["name"],
        "duration_s": round(chosen["duration_ms"] / 1000),
        "play_count": counts[chosen_id],
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
