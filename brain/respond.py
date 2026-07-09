"""Turns Claude's reply text into a WAV file.

Picks the TTS voice from the language actually present in the response text,
not just the STT-detected input language -- more robust if Claude ever
answers in the wrong language despite the system prompt.
"""
from __future__ import annotations

import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
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

# Splits on ./!/? followed by whitespace, but not when it's flanked by digits
# (guards against cutting "3.5" mid-decimal) or right after "a.m"/"p.m." --
# confirmed false-positive: "It's playing at 9:30 p.m. tonight" split into
# "...9:30 p.m." + "tonight", an awkward mid-thought cut. See speak_reply.
_SENTENCE_SPLIT_RE = re.compile(r"(?<![0-9])(?<![apAP]\.[mM])[.!?](?!\d)\s+")
_MIN_FIRST_SENTENCE_CHARS = 8  # skip the overlap for a too-short leading fragment


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


def _split_first_sentence(text: str) -> tuple[str, str] | None:
    """Return (first_sentence, rest) at the first real sentence boundary, or
    None if there isn't one worth splitting on (single-sentence reply, or a
    leading fragment too short to bother overlapping -- see speak_reply).
    """
    for match in _SENTENCE_SPLIT_RE.finditer(text):
        first, rest = text[: match.end()].strip(), text[match.end() :].strip()
        if len(first) >= _MIN_FIRST_SENTENCE_CHARS and rest:
            return first, rest
    return None


def speak_reply(text: str, out_device: Device) -> float:
    """Synthesize and play `text`, returning time-to-first-audio in seconds.

    For a multi-sentence reply, synthesizes and plays the first sentence
    immediately, while the rest synthesizes concurrently in the background --
    so the assistant starts talking without waiting for the full reply's TTS
    to finish. A single-sentence reply (the common case, per this project's
    system prompt keeping replies short) has no second chunk to overlap with,
    so it falls back to a plain synthesize-then-play with no added
    complexity. (A full streaming pipeline synthesizing sentence-by-sentence
    straight from Claude's token stream was considered and rejected -- see
    the design review: most replies are one sentence anyway, so it wouldn't
    have helped, and it risked speaking Claude's tool-call narration before
    its final answer was known.)
    """
    t_start = time.monotonic()
    split = _split_first_sentence(text)
    if split is None:
        wav = synthesize_reply(text)
        t_first_audio = time.monotonic() - t_start
        play_wav(wav, out_device)
        wav.unlink(missing_ok=True)
        return t_first_audio

    first, rest = split
    first_wav = synthesize_reply(first)
    t_first_audio = time.monotonic() - t_start
    with ThreadPoolExecutor(max_workers=1) as pool:
        rest_future = pool.submit(synthesize_reply, rest)
        play_wav(first_wav, out_device)
        rest_wav = rest_future.result()
    first_wav.unlink(missing_ok=True)
    play_wav(rest_wav, out_device)
    rest_wav.unlink(missing_ok=True)
    return t_first_audio
