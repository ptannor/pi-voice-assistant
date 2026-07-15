"""Speech-to-text via Groq's hosted Whisper API (see config.py for why Groq)."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from groq import Groq

from .config import GROQ_API_KEY, HOUSEHOLD_FAMILY_NAMES_EN, HOUSEHOLD_FAMILY_NAMES_HE, STT_MODEL
from .language import HEBREW_RE

# Whisper's `prompt` param biases transcription toward vocabulary it contains
# -- doesn't need to be a real transcript, just representative text. Confirmed
# useful: an unhinted transcription mangled a real family member's uncommon
# name into a completely different, unrelated-sounding word.
_FAMILY_NAME_PROMPTS = {
    "en": f"Family members: {HOUSEHOLD_FAMILY_NAMES_EN}." if HOUSEHOLD_FAMILY_NAMES_EN else None,
    "he": f"בני המשפחה: {HOUSEHOLD_FAMILY_NAMES_HE}." if HOUSEHOLD_FAMILY_NAMES_HE else None,
}

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


# Whisper's well-documented hallucination on silence/near-silence -- it's
# trained on a lot of subtitled video, so quiet or too-short audio (e.g. the
# recording window opening right as the wake word finishes, before the user
# has actually started talking) gets "transcribed" as one of a small set of
# stock captions instead of coming back empty. Confirmed in testing: saying
# "Alexa" without pausing before speaking produced a transcript of just
# "תודה" ("thank you") with no such word actually said.
_HALLUCINATION_PHRASES = {
    "thank you", "thanks for watching", "thank you for watching", "bye", "you", "i'm sorry",
    "תודה", "תודה רבה", "תודה שצפיתם",
}


def _is_likely_hallucination(text: str) -> bool:
    return text.strip().lower().rstrip(".!") in _HALLUCINATION_PHRASES


def _transcribe_forced(client: Groq, wav_path: Path, language: str) -> tuple[str, float]:
    """Transcribe with `language` forced (not auto-detected).

    Returns (text, confidence) -- confidence is the mean `avg_logprob` across
    segments (closer to 0 = more confident; very negative = the model wasn't
    sure this was really speech in this language).
    """
    kwargs = {}
    prompt = _FAMILY_NAME_PROMPTS.get(language)
    if prompt:
        kwargs["prompt"] = prompt
    with open(wav_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(wav_path.name, f.read()),
            model=STT_MODEL,
            response_format="verbose_json",
            language=language,
            **kwargs,
        )
    segments = result.segments or []
    confidence = sum(seg["avg_logprob"] for seg in segments) / len(segments) if segments else float("-inf")
    return result.text.strip(), confidence


def transcribe(wav_path: Path, forced_language: str | None = None) -> tuple[str, str]:
    """Return (text, language) -- language is "he" or "en".

    Auto-detection turned out unreliable for this household's short, casual
    bilingual utterances -- confirmed in testing: Groq's own acoustic language
    field sometimes says "English" for genuine Hebrew speech (not just
    mis-rendering the text, actually misjudging the language), so there's no
    reliable signal to even know a retry is needed. Instead of trusting
    auto-detect at all, this forces a transcription in *both* languages (in
    parallel, to keep latency down) and picks whichever one Whisper's own
    confidence score (avg_logprob) says is the more coherent result -- a
    signal from the model itself, not a text-pattern heuristic.

    Pass `forced_language` ("he" or "en") to skip that dual-transcription
    entirely and just force that language directly -- for locking a
    conversation to whichever language its first turn determined, so
    follow-up turns get the same forced-language accuracy benefit at half
    the Groq calls, without needing a second wake word per language.
    """
    if not GROQ_API_KEY:
        raise TranscriptionError("GROQ_API_KEY not set -- add it to .env")

    client = Groq(api_key=GROQ_API_KEY)
    try:
        if forced_language:
            text, _ = _transcribe_forced(client, wav_path, forced_language)
            language = forced_language
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_en = pool.submit(_transcribe_forced, client, wav_path, "en")
                future_he = pool.submit(_transcribe_forced, client, wav_path, "he")
                text_en, confidence_en = future_en.result()
                text_he, confidence_he = future_he.result()
            text, language = (text_he, "he") if confidence_he >= (confidence_en - 0.15) else (text_en, "en")
    except Exception as exc:
        raise TranscriptionError(f"Groq transcription failed: {exc}") from exc

    # Defensive phonetic correction: "it's so" is a classic Whisper mishearing of the Hebrew command "עצור" (stop)
    clean_text = text.replace("!", "").replace(".", "").replace(",", "").strip().lower()
    if clean_text in ("it's so", "its so", "it is so"):
        text = "עצור"
        language = "he"

    # Defensive final check: trust an unambiguous Hebrew script even if the
    # English-forced attempt somehow scored higher confidence (or was forced).
    if language == "en" and HEBREW_RE.search(text):
        language = "he"

    if _is_sound_effect_caption(text) or _is_likely_hallucination(text):
        text = ""
    return text, language
