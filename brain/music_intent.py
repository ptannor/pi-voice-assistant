"""Haiku-based classification of likely music-control intent for a short,
possibly STT-garbled utterance spoken while music is currently playing.

Built because prompt-only steering inside the main conversational system
prompt proved unreliable in practice for exactly this case (see the garbled-
input guidance in brain/llm.py's SYSTEM_PROMPT): across repeated identical
garbled inputs with music playing, the general-purpose conversational call
inconsistently defaulted to small talk, an unrelated tool, or a fresh (and
unpredictable) song search instead of the much simpler skip/stop/volume
action actually intended -- confirmed live, with a clean (non-contaminated)
test showing only 1 of 4 identical inputs producing the right action. A
single-purpose classifier asked to choose among only a few categories is a
far narrower decision than free-form conversation and performs far more
consistently -- the same principle behind brain/classify.py's reminder
categorization.
"""
from __future__ import annotations

import re

import anthropic

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL

CATEGORIES = ("skip", "stop", "volume_up", "volume_down")
UNCLEAR = "unclear"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


_SYSTEM = """The household is currently listening to music or a podcast on a
voice assistant, and just said something -- but speech-to-text may have
badly garbled it (short commands are especially prone to this). Classify
their most likely intent into exactly one of:

- skip: they want to skip to a different/the next track
- stop: they want to stop or pause the music
- volume_up: they want it louder
- volume_down: they want it quieter
- unclear: none of the above seems like a plausible match, or the text
  reads as a genuinely coherent, unrelated request that has nothing to do
  with controlling music

Judge by which category is the most plausible thing a household member
would actually say near a speaker while music plays, including accounting
for common Hebrew/English speech-to-text mishearings -- not just literal
exact word matches. If it's genuinely ambiguous between two categories, or
doesn't fit any of them, answer unclear rather than guessing.

Answer with exactly one word: skip, stop, volume_up, volume_down, or unclear. Nothing else."""

_CATEGORY_RE = re.compile(r"skip|stop|volume_up|volume_down|unclear")


def classify_music_intent(text: str) -> str:
    """Best-effort classification -- returns "unclear" (never raises) on
    any failure, so a transient API issue just falls through to the normal
    conversational turn instead of blocking anything."""
    try:
        client = _get_client()
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=10,
            system=_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        result = "".join(b.text for b in response.content if b.type == "text").strip().lower()
        match = _CATEGORY_RE.search(result)
        return match.group(0) if match else UNCLEAR
    except Exception:
        return UNCLEAR


def execute(category: str, language: str) -> tuple[str, str]:
    """Runs the deterministic action for a classified category.

    Returns (reply, tool_label) -- reply is silent for skip/stop (matching
    the existing convention for those actions elsewhere in this codebase),
    a real spoken confirmation/error for volume (also matching the
    existing convention -- see brain/llm.py's _volume_fallback_reply).
    tool_label mirrors the equivalent real tool's name (language-suffixed
    for skip/stop, matching brain/tools.py's naming) purely so
    wake_word_daemon.py's post-turn handling (suppress the pre-conversation
    resume, keep listening for a likely follow-up) recognizes this exactly
    like a normal skip/stop/volume tool call, with no special case needed
    there.
    """
    from . import spotify, volume

    suffix = "hebrew" if language == "he" else "english"

    if category == "skip":
        try:
            spotify.skip_track("next")
        except spotify.SpotifyError:
            pass
        return "", f"skip_track_{suffix}"

    if category == "stop":
        try:
            spotify.stop()
        except spotify.SpotifyError:
            pass
        return "", f"stop_music_{suffix}"

    if category in ("volume_up", "volume_down"):
        try:
            (volume.volume_up if category == "volume_up" else volume.volume_down)()
            reply = "בוצע." if language == "he" else "Done."
        except volume.VolumeError:
            reply = "לא הצלחתי לשנות את העוצמה." if language == "he" else "I couldn't change the volume."
        return reply, category

    return "", ""
