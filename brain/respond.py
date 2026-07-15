"""Turns Claude's reply text into a WAV file.

Picks the TTS voice from the language actually present in the response text,
not just the STT-detected input language -- more robust if Claude ever
answers in the wrong language despite the system prompt.
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


def synthesize_reply(text: str) -> Path:
    """Synthesize `text` to a temp WAV file and return its path.

    LLM-generated Hebrew is everyday conversational language, not the
    Biblical/liturgical register that needs nikud (see hebrew_tts/nakdan.py's
    docstring) -- so this always uses plain text, no vocalize() call.
    """
    language = detect_language(text)
    voices, pitches = (_FUNNY_VOICES, _FUNNY_PITCH) if is_funny_voice_enabled() else (_NORMAL_VOICES, _NORMAL_PITCH)
    output_path = Path(tempfile.mktemp(suffix=".wav"))
    synthesize_to_wav(text, output_path, voice=voices[language], pitch=pitches[language])
    return output_path


def speak_reply(text: str, out_device: Device) -> float:
    """Synthesize and play `text` as a single continuous wav file.

    This prevents audio hardware clicks, pops, or static noise in the middle of
    replies (like jokes) that occurs when opening/closing the audio stream
    multiple times for separate sentence chunks.
    """
    text = (text or "").strip()
    if not text:
        return 0.0
    t_start = time.monotonic()
    wav = synthesize_reply(text)
    t_first_audio = time.monotonic() - t_start
    play_wav(wav, out_device)
    wav.unlink(missing_ok=True)
    return t_first_audio

