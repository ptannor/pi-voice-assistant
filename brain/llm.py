"""Conversational responses via Anthropic's Claude API."""
from __future__ import annotations

import anthropic

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from .tools import TOOLS, execute_tool

SYSTEM_PROMPT = """You are Menachem Mendel, a friendly voice assistant for a home.
Your replies are read aloud by text-to-speech, so keep them short and
conversational -- a sentence or two, not a lecture.

Always reply in the same language the user just spoke to you in: if they
spoke English, reply in English; if they spoke Hebrew, reply in Hebrew.

Use the available tools for anything they cover (timers, music). Don't claim
to have done something physical/real-world unless a tool result actually
confirms it.
"""

_LANGUAGE_NAMES = {"he": "Hebrew", "en": "English"}
_MAX_TOOL_ROUNDS = 3  # safety cap against a runaway tool-call loop


class BrainError(Exception):
    pass


def ask(user_text: str, language: str, history: list[dict] | None = None) -> tuple[str, list[dict]]:
    """`language` is "he" or "en" (see brain/language.py).

    `history` is prior turns of the same conversation (None/empty for a new
    one); returns (reply_text, updated_history) so callers can keep passing
    the running history back in for follow-up turns within one session.
    """
    if not ANTHROPIC_API_KEY:
        raise BrainError("ANTHROPIC_API_KEY not set -- add it to .env")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    language_name = _LANGUAGE_NAMES[language]
    messages = (history or []) + [
        {"role": "user", "content": f"[The user spoke in {language_name}] {user_text}"}
    ]

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=300, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages
        )
        rounds = 0
        while response.stop_reason == "tool_use" and rounds < _MAX_TOOL_ROUNDS:
            rounds += 1
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {"type": "tool_result", "tool_use_id": block.id, "content": execute_tool(block.name)}
                for block in response.content
                if block.type == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
            response = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=300, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages
            )
    except Exception as exc:
        raise BrainError(f"Claude request failed: {exc}") from exc

    reply = "".join(block.text for block in response.content if block.type == "text").strip()
    messages.append({"role": "assistant", "content": response.content})
    return reply, messages
