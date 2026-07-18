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
#
# The Hebrew prompt also includes "מה השעה" (what time is it) -- confirmed a
# quick/quiet utterance of this very common question was consistently
# misheard as the English name "Masha" (see the defensive correction below
# for the cases that still get through anyway).
#
# Deliberately NOT prefixed with a "שאלה נפוצה:" ("common question:") label
# like an earlier version of this prompt had -- confirmed that exact label
# phrase was what Whisper hallucinated back verbatim as the entire
# "transcription" on quiet/unclear audio (Whisper's prompt is conditioning
# context, not just a vocabulary hint, and it can echo distinctive prompt
# text instead of admitting the audio wasn't clear speech). "שאלה נפוצה" is
# also now in _HALLUCINATION_PHRASES below as defense in depth, but the real
# fix is not handing the model an easily-echoable phrase in the first place.
_FAMILY_NAME_PROMPTS = {
    "en": f"Family members: {HOUSEHOLD_FAMILY_NAMES_EN}." if HOUSEHOLD_FAMILY_NAMES_EN else None,
    "he": (
        f"בני המשפחה: {HOUSEHOLD_FAMILY_NAMES_HE}. מה השעה?"
        if HOUSEHOLD_FAMILY_NAMES_HE
        else "מה השעה?"
    ),
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
    "תודה", "תודה רבה", "תודה שצפיתם", "beep", "beep beep", "beep beep beep",
    # "common question" -- not a real thing anyone would say aloud; was
    # getting echoed back from the Hebrew prompt's own conditioning text on
    # quiet/unclear audio before that prompt was reworded (see
    # _FAMILY_NAME_PROMPTS above). Kept here too as defense in depth.
    "שאלה נפוצה",
}


def _is_likely_hallucination(text: str) -> bool:
    clean = text.replace(".", "").replace("!", "").replace(",", "").strip().lower()
    return clean in _HALLUCINATION_PHRASES


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


def transcribe(wav_path: Path, conversation_language: str | None = None) -> tuple[str, str]:
    """Return (text, language) -- language is "he" or "en".

    Auto-detection turned out unreliable for this household's short, casual
    bilingual utterances -- confirmed in testing: Groq's own acoustic language
    field sometimes says "English" for genuine Hebrew speech (not just
    mis-rendering the text, actually misjudging the language), so there's no
    reliable signal to even know a retry is needed. Without `conversation_language`,
    this falls back to forcing a transcription in *both* languages (in
    parallel, to keep latency down) and picking whichever one Whisper's own
    confidence score (avg_logprob) says is the more coherent result -- a
    signal from the model itself, not a text-pattern heuristic. Still
    confirmed unreliable in both directions though (Hebrew "מה השעה" heard as
    English "Masha", English "summertime sadness" heard as Hebrew gibberish),
    which is why callers should prefer `conversation_language` whenever one's
    available instead of leaning on this fallback.

    `conversation_language` fixes the language for an ENTIRE conversation
    (every turn, not just the first), derived from which wake word started it
    ("alexa" vs "hey_jarvis"/"mendy" -- see wake_word_daemon.py) -- a
    deterministic signal, not a guess, so it's safe to hold constant for the
    whole conversation. This is different from a prior, since-removed
    `forced_language` param that locked onto whichever language *STT itself*
    guessed on turn 1 (not a wake word) and was confirmed broken: a household
    member alternating Hebrew/English sentence-by-sentence got follow-up
    turns forced into whatever STT happened to guess first, producing
    garbled nonsense. The difference is the source of truth -- a wake word
    can't misdetect its own language, an STT confidence score can. Per
    product decision, mid-conversation language switches aren't supported at
    all now: say the wake word for the language you want, every time.
    """
    if not GROQ_API_KEY:
        raise TranscriptionError("GROQ_API_KEY not set -- add it to .env")

    client = Groq(api_key=GROQ_API_KEY)
    try:
        if conversation_language:
            text, _ = _transcribe_forced(client, wav_path, conversation_language)
            language = conversation_language
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_en = pool.submit(_transcribe_forced, client, wav_path, "en")
                future_he = pool.submit(_transcribe_forced, client, wav_path, "he")
                text_en, confidence_en = future_en.result()
                text_he, confidence_he = future_he.result()
            text, language = (text_he, "he") if confidence_he >= (confidence_en - 0.15) else (text_en, "en")
            # Logged (not just available in a debugger) because the 0.15 Hebrew
            # bias below is a guess, not a measured threshold -- confirmed wrong
            # in BOTH directions (Hebrew "מה השעה" heard as English "Masha", and
            # English "summertime sadness" heard as Hebrew phonetic gibberish).
            # Tightening/removing that bias needs real confidence numbers from
            # cases like those, not another guess.
            print(f"[stt] confidence en={confidence_en:.3f} he={confidence_he:.3f} -> picked {language}", flush=True)
    except Exception as exc:
        raise TranscriptionError(f"Groq transcription failed: {exc}") from exc

    # Defensive phonetic correction: classic Whisper English mishearings of the Hebrew command "עצור" (stop)
    clean_text = text.replace("!", "").replace(".", "").replace(",", "").strip().lower()
    if clean_text in (
        "it's so", "its so", "it is so", "so",
        "what's so", "whats so", "what so", "but so",
        "also", "all so", "how so", "how's so", "that's so", "thats so"
    ):
        text = "עצור"
        language = "he"

    # Defensive phonetic correction: classic Whisper English mishearing of the
    # Hebrew question "מה השעה" (what time is it) as the name "Masha" -- see
    # the prompt hint above; this catches the cases that get through anyway.
    if clean_text in ("masha", "masha?"):
        text = "מה השעה"
        language = "he"

    # Defensive correction: Whisper's well-documented tendency to hallucinate
    # a stock crude/profane word for a brief, unclear, or non-speech sound
    # right at the very start of a clip (often the tail of the wake word, or
    # the ack chime bleeding into the recording's lead-in) instead of just
    # transcribing silence there -- the rest of the sentence usually comes
    # through perfectly clearly. Confirmed live twice, with two different
    # genuine requests behind it ("tell me a joke", "what's the weather...")
    # and a different real leading word each time -- so unlike the עצור/Masha
    # corrections above, there's no single word to correct *to* here. Just
    # dropping the hallucinated leading word and letting Claude's own
    # understanding handle the remaining (now clean) fragment is safer than
    # guessing what was actually said.
    leading_word_match = re.match(r"^(\S+)\s+(.+)$", text)
    if leading_word_match:
        first_word = leading_word_match.group(1).strip(".,!?").lower()
        if re.fullmatch(r"fuck(ing|er)?|shit(ty)?|damn|goddamn|hell|bitch|ass(hole)?|crap|f\*+k?|s\*+t?", first_word):
            text = leading_word_match.group(2)
            clean_text = text.replace("!", "").replace(".", "").replace(",", "").strip().lower()

    # Defensive phonetic correction: the specific case above of "tell me a
    # joke" losing its leading word this way (or being misheard as some
    # other unrelated word in its place) -- "me a joke" alone, or "<word> me
    # a joke", is overwhelmingly always this same request.
    joke_request_match = re.match(r"^(?:(\S+) )?me a joke$", clean_text)
    if joke_request_match and joke_request_match.group(1) not in ("tell", "give", "make", "say"):
        text = "tell me a joke"

    # Defensive final check: trust an unambiguous Hebrew script even if the
    # English-forced attempt somehow scored higher confidence (or was forced).
    if language == "en" and HEBREW_RE.search(text):
        language = "he"

    if _is_sound_effect_caption(text) or _is_likely_hallucination(text):
        text = ""
    return text, language
