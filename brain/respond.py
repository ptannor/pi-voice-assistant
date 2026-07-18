"""Turns Claude's reply text into a WAV file.

Picks the TTS voice from the conversation's actual language when the caller
knows it (see `language` param below) -- brain/llm.py's `ask()` now
guarantees its reply matches that language via a prefilled retry + canned
fallback, so re-detecting from the reply text would just reintroduce a
classifier that's a weaker signal (it can misclassify a reply that quotes a
short phrase in the other language). Falls back to detect_language(text)
only when no language is given.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from audio_check.devices import Device
from audio_check.player import play_wav
from hebrew_tts.synth import synthesize_to_wav

from .language import detect_language
from .mode import is_funny_voice_enabled

_NORMAL_VOICES = {"he": "he-IL-AvriNeural", "en": "en-US-GuyNeural"}
_NORMAL_PITCH = {"he": "+0Hz", "en": "+0Hz"}

# en-US-AnaNeural is Microsoft's actual child voice (tagged "Cute"/"Cartoon").
# he-IL has no child voice at all (only the two adult Avri/Hila ones), so
# Hebrew instead pitch-shifts the female adult voice up as an approximation.
# See mode.py -- toggled by the set_voice_mode tool, off by default.
_FUNNY_VOICES = {"he": "he-IL-HilaNeural", "en": "en-US-AnaNeural"}
_FUNNY_PITCH = {"he": "+100Hz", "en": "+0Hz"}


def synthesize_reply(text: str, language: str | None = None) -> Path:
    """Synthesize `text` to a temp WAV file and return its path.

    `language`, if given, is the conversation's actual language (e.g. from
    brain/llm.py's `ask()`, which now guarantees the reply matches it via a
    prefilled retry + canned fallback) -- pass it when known, since
    detect_language() on the reply text is a same fallback, not a stronger
    signal: it can misclassify a reply whose dominant language quotes a
    short phrase in the other one (confirmed: an English clarification that
    quoted the user's Hebrew phrase back). Omit it only when the caller
    truly doesn't know (falls back to detect_language(text)).

    LLM-generated Hebrew is everyday conversational language, not the
    Biblical/liturgical register that needs nikud (see hebrew_tts/nakdan.py's
    docstring) -- so this always uses plain text, no vocalize() call.
    """
    language = language or detect_language(text)
    voices, pitches = (_FUNNY_VOICES, _FUNNY_PITCH) if is_funny_voice_enabled() else (_NORMAL_VOICES, _NORMAL_PITCH)
    output_path = Path(tempfile.mktemp(suffix=".wav"))
    synthesize_to_wav(text, output_path, voice=voices[language], pitch=pitches[language])
    return output_path


def speak_reply_chunks(text: str, language: str | None = None) -> tuple[list[Path], float]:
    """Synthesize reply to a single continuous wav file inside a list.

    This prevents audio hardware clicks, pops, or static noise in the middle of
    replies (like jokes) that occurs when opening/closing the audio stream
    multiple times for separate sentence chunks, while keeping compatibility
    with the daemon's chunk play loop.
    """
    text = (text or "").strip()
    if not text:
        return [], 0.0
    t_start = time.monotonic()
    wav = synthesize_reply(text, language=language)
    t_first_audio = time.monotonic() - t_start
    return [wav], t_first_audio


def speak_reply(text: str, out_device: Device, language: str | None = None) -> float:
    """Synthesize and play `text` as a single continuous wav file.

    This prevents audio hardware clicks, pops, or static noise in the middle of
    replies (like jokes) that occurs when opening/closing the audio stream
    multiple times for separate sentence chunks.
    """
    text = (text or "").strip()
    if not text:
        return 0.0
    chunks, t_first_audio = speak_reply_chunks(text, language=language)
    for wav in chunks:
        play_wav(wav, out_device)
        wav.unlink(missing_ok=True)
    return t_first_audio

