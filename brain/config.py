"""API keys for the STT/LLM pipeline, loaded from a local `.env` (gitignored).

Personal keys only -- this is a personal public repo, not Check Point work.
Never point ANTHROPIC_API_KEY/GROQ_API_KEY at a corporate gateway or
credential (see the UV_INDEX incident in the README's troubleshooting section
for why that's a hard rule here, not just a suggestion).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PI_CONFIG_PATH = Path(__file__).parent.parent / ".pi-config"


def _read_pi_config_value(key: str) -> str | None:
    # Same gitignored `.pi-config` file shabbat/config.py reads location out
    # of -- personal info like this can't live in the (public) repo itself.
    if not _PI_CONFIG_PATH.exists():
        return None
    for line in _PI_CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


# e.g. "Givat Shmuel, Israel" -- lets Claude give location-appropriate answers
# (emergency numbers, "what's nearby", etc.) instead of defaulting to US-centric
# ones. Set via `.pi-config`, never hardcoded here (see README).
HOUSEHOLD_LOCATION = _read_pi_config_value("HOUSEHOLD_LOCATION")

# IANA tz name (e.g. "Asia/Jerusalem") for telling Claude the actual current
# date/time -- an LLM has no built-in clock, and without this it has no way
# to know "today", let alone sanity-check something like a claimed showtime
# against what time it actually is. Defaults to Israel since that's this
# household's only supported timezone so far (see SHABBAT_ISRAEL); override
# via `.pi-config` if that ever changes.
HOUSEHOLD_TIMEZONE = _read_pi_config_value("HOUSEHOLD_TIMEZONE") or "Asia/Jerusalem"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
# Serper.dev web search -- 2,500 free queries, no card required, then
# pay-as-you-go from $0.001/query. Chosen over Anthropic's native web search
# tool ($0.01/search, but can't intercept the query to cache it -- it runs
# server-side inside Anthropic's own infrastructure) and over Brave Search
# API (similar cost, needs a card on file). Public unauthenticated search
# instances (SearXNG) were tried first and confirmed dead -- bot-blocked,
# rate-limited, or expired domains across every instance tested.
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")

# Haiku: fast/cheap, appropriate for short spoken Q&A read aloud by TTS.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Groq's hosted Whisper -- chosen over OpenAI's own Whisper API (cheaper,
# faster) and over self-hosting ivrit.ai's Hebrew-tuned models (better Hebrew
# accuracy, but Hebrew-only and requires running your own GPU endpoint --
# not worth the ops overhead for a hobby project that also needs English).
STT_MODEL = "whisper-large-v3-turbo"
