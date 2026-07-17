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

# Free-text list of nearby cities/areas that should be treated as local too
# (e.g. "Ramat Gan, Bnei Brak, Petach Tikva, Kiryat Ono, Tel Aviv -- all
# ~15 min away"). Without this, Claude over-indexed on HOUSEHOLD_LOCATION
# being the *only* local place -- confirmed: asked about a mall in a
# neighboring city, it got confused and invented a claim that search results
# for that real mall must be about a different, unverified place, purely
# because the city name didn't match HOUSEHOLD_LOCATION exactly.
HOUSEHOLD_NEARBY_AREAS = _read_pi_config_value("HOUSEHOLD_NEARBY_AREAS")

# IANA tz name (e.g. "Asia/Jerusalem") for telling Claude the actual current
# date/time -- an LLM has no built-in clock, and without this it has no way
# to know "today", let alone sanity-check something like a claimed showtime
# against what time it actually is. Defaults to Israel since that's this
# household's only supported timezone so far (see SHABBAT_ISRAEL); override
# via `.pi-config` if that ever changes.
HOUSEHOLD_TIMEZONE = _read_pi_config_value("HOUSEHOLD_TIMEZONE") or "Asia/Jerusalem"

# Path to a custom-trained wake-word model (.onnx/.tflite), e.g. from
# training "Mendy" via openWakeWord's Colab notebook -- see README's Wake
# word section. Defaults to the trained model checked into models/mendy.onnx
# if present; override via .pi-config to point at a different one (or delete
# models/mendy.onnx to fall back to the pretrained "hey_jarvis" placeholder).
_DEFAULT_WAKE_WORD_MODEL = Path(__file__).parent.parent / "models" / "mendy.onnx"
WAKE_WORD_MODEL_PATH = _read_pi_config_value("WAKE_WORD_MODEL_PATH") or (
    str(_DEFAULT_WAKE_WORD_MODEL) if _DEFAULT_WAKE_WORD_MODEL.exists() else None
)

# Comma-separated family member names, in each language they'd actually be
# spoken/read in -- fed to Whisper as a transcription hint (see brain/stt.py)
# so it's biased toward recognizing them correctly. Confirmed necessary: an
# uncommon family member's name got transcribed as a completely different,
# unrelated-sounding word with no hint at all.
HOUSEHOLD_FAMILY_NAMES_EN = _read_pi_config_value("HOUSEHOLD_FAMILY_NAMES_EN")
HOUSEHOLD_FAMILY_NAMES_HE = _read_pi_config_value("HOUSEHOLD_FAMILY_NAMES_HE")

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

# Spotify Web API credentials for music playback (see brain/spotify.py). These
# are the standard spotipy env var names -- register a free app at
# developer.spotify.com to get the client id/secret, and set the redirect URI
# to match what's registered there. Note: controlling playback also requires a
# Spotify Premium account and an active Spotify Connect device to play on.
SPOTIPY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI")

# Substring to match against a Spotify Connect device's name (see
# brain/spotify.py's _active_device_id), e.g. "Living Room" or the household's
# actual speaker's name in the Spotify app. Without this, when no device is
# currently marked active, playback fell back to whichever device happened to
# be first in Spotify's own (unspecified, not "most recent"/"nearest") device
# list -- confirmed: played on a household member's phone instead of the
# intended speaker. Leave unset to keep that arbitrary-first-device fallback.
SPOTIFY_DEVICE_NAME = _read_pi_config_value("SPOTIFY_DEVICE_NAME")

# Haiku: fast/cheap, appropriate for short spoken Q&A read aloud by TTS.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Google Calendar (see brain/gcal.py) -- a dedicated secondary calendar for
# Mendy's reminders, accessed via a service account rather than the
# spotipy-style interactive OAuth flow: a service account never needs
# interactive re-auth (no browser, ever, matching this file's non-interactive
# constraint for anything running inside the daemon), and only sees calendars
# explicitly shared with it -- narrower access than an OAuth grant on the
# household's whole Google account. Path to the service account's downloaded
# JSON key.
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

# The "Mendy" calendar's id (Google Calendar -> that calendar's Settings ->
# "Calendar ID", looks like an email address) -- set via .pi-config after
# creating the calendar and sharing it with the service account's email (see
# brain/gcal.py's module docstring for the one-time setup steps).
MENDY_CALENDAR_ID = _read_pi_config_value("MENDY_CALENDAR_ID")

# Telegram bot token from @BotFather (see telegram_bot_daemon.py) -- chosen
# over WhatsApp for a bot that must run unattended on a home Pi: one token,
# official long-polling API, no Meta business review, no dedicated phone
# number.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Comma-separated numeric Telegram chat ids allowed to talk to the bot --
# checked on every incoming message so a stranger who finds the bot's
# username gets no tool access at all, not even a stub reply. Get a chat id
# by messaging the bot once and checking the console log it prints for
# unrecognized senders, or via @userinfobot.
_telegram_ids_raw = _read_pi_config_value("TELEGRAM_ALLOWED_CHAT_IDS") or ""
TELEGRAM_ALLOWED_CHAT_IDS = [s.strip() for s in _telegram_ids_raw.split(",") if s.strip()]

# Minutes of lead time before a calendar event's start to speak its reminder
# (0 = fire exactly at the event's own time, right for "take antibiotics at
# 8am"). Override via .pi-config for e.g. appointments where a few minutes'
# notice is more useful than none.
REMINDER_LEAD_MINUTES = int(_read_pi_config_value("REMINDER_LEAD_MINUTES") or "0")

# Groq's hosted Whisper -- chosen over OpenAI's own Whisper API (cheaper,
# faster) and over self-hosting ivrit.ai's Hebrew-tuned models (better Hebrew
# accuracy, but Hebrew-only and requires running your own GPU endpoint --
# not worth the ops overhead for a hobby project that also needs English).
STT_MODEL = "whisper-large-v3-turbo"
