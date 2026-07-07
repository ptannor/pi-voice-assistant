"""Shared English/Hebrew detection, used for both the transcribed question
(picking what to tell Claude) and its reply (picking the TTS voice).

Text-only Hebrew-Unicode detection isn't enough on its own: Whisper-family
models sometimes transliterate Hebrew speech into phonetic Latin-letter
"gibberish" text instead of Hebrew script (confirmed in testing -- a Hebrew
question came back as text like "Tudad ma'ashem sheli?"), which a
text-only check would misread as English. Groq's own acoustic `language`
field (detected from the audio itself, independent of how it renders the
text) gets this right -- confirmed it returns the full word "Hebrew"
(capitalized), not an ISO code -- so it's used as the primary signal here,
normalized loosely (case-insensitive prefix match) rather than an exact
dict lookup, in case Groq ever sends a different format. Hebrew-Unicode text
is kept as a fallback for callers with no acoustic hint (e.g. detecting the
language of Claude's own generated reply text, which is never transliterated
gibberish since it's produced directly as text, not transcribed from audio).
"""
from __future__ import annotations

import re

HEBREW_RE = re.compile(r"[֐-׿]")


def detect_language(text: str, acoustic_hint: str | None = None) -> str:
    """Return "he" or "en".

    `acoustic_hint` is Groq's transcription-response `language` field, when
    available -- takes priority over the text itself (see module docstring).
    """
    if acoustic_hint and acoustic_hint.strip().lower().startswith("he"):
        return "he"
    return "he" if HEBREW_RE.search(text) else "en"
