"""Runtime toggle for the kids' "funny voice" easter egg.

A plain in-memory flag, not persisted config -- resets to off whenever the
daemon restarts. Switched via the set_voice_mode tool (see tools.py), read by
llm.py (whether to append the silly sign-off phrase) and respond.py (which
TTS voice/pitch to use).
"""
from __future__ import annotations

_funny_voice_enabled = False


def is_funny_voice_enabled() -> bool:
    return _funny_voice_enabled


def set_funny_voice(enabled: bool) -> None:
    global _funny_voice_enabled
    _funny_voice_enabled = enabled
