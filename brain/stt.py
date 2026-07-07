"""Speech-to-text via Groq's hosted Whisper API (see config.py for why Groq)."""
from __future__ import annotations

import re
from pathlib import Path

from groq import Groq

from .config import GROQ_API_KEY, STT_MODEL
from .language import HEBREW_RE, detect_language

_BRACKETED_RE = re.compile(r"^[\[(].*[\])]$")


class TranscriptionError(Exception):
    pass


def _is_sound_effect_caption(text: str) -> bool:
    """Whisper sometimes transcribes non-speech audio (a phone ringing, music,
    etc.) as a bracketed or all-caps caption (e.g. "[MUSIC]", "PHONE RINGS")
    instead of real words -- confirmed in testing: ambient noise during the
    follow-up listening window got treated as a real query this way. Catch
    the common patterns so a stray noise doesn't get sent to Claude as if the
    user said it.
    """
    if _BRACKETED_RE.match(text):
        return True
    # .isupper() (not `text == text.upper()`) specifically requires at least one
    # cased character -- Hebrew has no letter case at all, so `==` against
    # .upper() is trivially true for any Hebrew text and would wrongly discard it.
    return len(text.split()) >= 2 and text.isupper()


def _call_groq(client: Groq, wav_path: Path, language: str | None = None) -> tuple[str, str]:
    with open(wav_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(wav_path.name, f.read()),
            model=STT_MODEL,
            response_format="verbose_json",
            **({"language": language} if language else {}),
        )
    return result.text.strip(), result.language


def transcribe(wav_path: Path) -> tuple[str, str]:
    """Return (text, language) -- language is "he" or "en".

    Passes Groq's own acoustic `language` field into detect_language() as the
    primary signal (see language.py for why: it's detected from the audio
    itself, so it stays correct even when Whisper transliterates Hebrew
    speech into Latin-letter text instead of Hebrew script).

    If the acoustic field says Hebrew but the transcribed text has no Hebrew
    characters at all -- the signature of that transliteration bug -- retry
    once with the language forced to Hebrew. Forcing the language (instead of
    auto-detecting) makes Whisper commit to transcribing in that script
    rather than guessing a Latin-letter phonetic rendering.
    """
    if not GROQ_API_KEY:
        raise TranscriptionError("GROQ_API_KEY not set -- add it to .env")

    client = Groq(api_key=GROQ_API_KEY)
    try:
        text, acoustic_language = _call_groq(client, wav_path)
        looks_transliterated = acoustic_language.strip().lower().startswith("he") and not HEBREW_RE.search(
            text
        )
        if looks_transliterated:
            text, acoustic_language = _call_groq(client, wav_path, language="he")
    except Exception as exc:
        raise TranscriptionError(f"Groq transcription failed: {exc}") from exc

    if _is_sound_effect_caption(text):
        text = ""
    return text, detect_language(text, acoustic_hint=acoustic_language)
