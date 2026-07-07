"""Speech-to-text via Groq's hosted Whisper API (see config.py for why Groq)."""
from __future__ import annotations

from pathlib import Path

from groq import Groq

from .config import GROQ_API_KEY, STT_MODEL
from .language import detect_language


class TranscriptionError(Exception):
    pass


def transcribe(wav_path: Path) -> tuple[str, str]:
    """Return (text, language) -- language is "he" or "en".

    Groq auto-detects the spoken language internally to transcribe correctly,
    but we re-derive the language tag from the transcribed text ourselves
    (see language.py) rather than trust the response's own `language` field,
    since its exact string format ("he" vs "hebrew") isn't documented clearly
    enough to risk silently mislabeling every Hebrew question as English.
    """
    if not GROQ_API_KEY:
        raise TranscriptionError("GROQ_API_KEY not set -- add it to .env")

    client = Groq(api_key=GROQ_API_KEY)
    try:
        with open(wav_path, "rb") as f:
            result = client.audio.transcriptions.create(
                file=(wav_path.name, f.read()),
                model=STT_MODEL,
                response_format="verbose_json",
            )
    except Exception as exc:
        raise TranscriptionError(f"Groq transcription failed: {exc}") from exc

    text = result.text.strip()
    return text, detect_language(text)
