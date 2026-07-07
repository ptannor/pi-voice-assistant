"""Turns Claude's reply text into a WAV file.

Picks the TTS voice from the language actually present in the response text,
not just the STT-detected input language -- more robust if Claude ever
answers in the wrong language despite the system prompt.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from hebrew_tts.synth import synthesize_to_wav

from .language import detect_language

ENGLISH_VOICE = "en-US-GuyNeural"
HEBREW_VOICE = "he-IL-AvriNeural"
_VOICES = {"he": HEBREW_VOICE, "en": ENGLISH_VOICE}


def synthesize_reply(text: str) -> Path:
    """Synthesize `text` to a temp WAV file and return its path.

    LLM-generated Hebrew is everyday conversational language, not the
    Biblical/liturgical register that needs nikud (see hebrew_tts/nakdan.py's
    docstring) -- so this always uses plain text, no vocalize() call.
    """
    voice = _VOICES[detect_language(text)]
    output_path = Path(tempfile.mktemp(suffix=".wav"))
    synthesize_to_wav(text, output_path, voice=voice)
    return output_path
