"""Conversational responses via Anthropic's Claude API."""
from __future__ import annotations

import anthropic

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL

SYSTEM_PROMPT = """You are Menachem Mendel, a friendly voice assistant for a home.
Your replies are read aloud by text-to-speech, so keep them short and
conversational -- a sentence or two, not a lecture.

Always reply in the same language the user just spoke to you in: if they
spoke English, reply in English; if they spoke Hebrew, reply in Hebrew.
"""

_LANGUAGE_NAMES = {"he": "Hebrew", "en": "English"}


class BrainError(Exception):
    pass


def ask(user_text: str, language: str) -> str:
    """`language` is "he" or "en" (see brain/language.py)."""
    if not ANTHROPIC_API_KEY:
        raise BrainError("ANTHROPIC_API_KEY not set -- add it to .env")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    language_name = _LANGUAGE_NAMES[language]
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": f"[The user spoke in {language_name}] {user_text}"}
            ],
        )
    except Exception as exc:
        raise BrainError(f"Claude request failed: {exc}") from exc

    return "".join(block.text for block in response.content if block.type == "text").strip()
