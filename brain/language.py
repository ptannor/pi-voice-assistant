"""Shared English/Hebrew detection, used for both the transcribed question
(picking what to tell Claude) and its reply (picking the TTS voice).

Detecting from the actual text -- rather than trusting Groq's `language`
field on the transcription response -- avoids depending on an unverified API
contract (whether it returns "he" or "hebrew" isn't documented clearly, and
guessing wrong would silently mislabel every Hebrew question as English).
"""
from __future__ import annotations

import re

HEBREW_RE = re.compile(r"[֐-׿]")


def detect_language(text: str) -> str:
    """Return "he" or "en" based on whether Hebrew characters are present."""
    return "he" if HEBREW_RE.search(text) else "en"
