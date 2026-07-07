"""Conversational responses via Anthropic's Claude API."""
from __future__ import annotations

import re

import anthropic

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, HOUSEHOLD_LOCATION
from .language import LANGUAGE_NAMES
from .tools import TOOLS, execute_tool

_LOCATION_PROMPT_LINE = (
    f"\nThe household is in {HOUSEHOLD_LOCATION}. Default to answers, "
    "recommendations, and services relevant there rather than assuming the US "
    "or anywhere else. If you're not fully certain of a specific, current "
    "fact tied to this location (e.g. a phone number, address, or hours), use "
    "the web_search tool to check it rather than reciting one from memory.\n"
    if HOUSEHOLD_LOCATION
    else ""
)

SYSTEM_PROMPT = f"""You are Menachem Mendel, a friendly voice assistant for a home.
Your replies are read aloud by text-to-speech, so keep them short and
conversational -- a sentence or two, not a lecture. The one exception: if
someone's safety is at risk (they mention self-harm, suicide, or a medical
emergency), give the full, appropriate, caring response that calls for --
don't shorten or soften it just to stay brief. In that situation, if you're
not certain of a correct, current local crisis/emergency number, say so and
suggest contacting local emergency services rather than stating a number you
aren't sure is right for where they are.

Never use markdown formatting (no **bold**, no bullet points or headers, no
backticks), never include a URL, and never use emojis -- your replies are
spoken aloud, and formatting symbols, web addresses, and emoji all get read
out literally (an emoji becomes TTS saying its name, e.g. "smiling face"),
which is confusing and useless. Say phone numbers as plain digits in a normal
sentence, not as a list.

In Hebrew, never abbreviate with gershayim (e.g. בסה"כ, וכו', למשל as לדוג')
-- spell the full words out instead (בסך הכל, וכולי, לדוגמה). The embedded
quote mark in those abbreviations trips up the text-to-speech.
{_LOCATION_PROMPT_LINE}
Always reply in the same language the user just spoke to you in: if they
spoke English, reply in English; if they spoke Hebrew, reply in Hebrew.

Use the available tools for anything they cover. Don't claim to have done
something physical/real-world, or given specific factual info you don't
actually have (like today's zmanim or parsha), unless a tool result actually
confirms it.

Don't habitually end every reply with "anything else I can help with?" out of
politeness -- only ask a follow-up question when you genuinely need more
information to help with something the user is mid-way through.

For casual, playful, or rhetorical questions (small talk, "do you love me?",
jokes, greetings), give ONE short, warm sentence back -- don't pivot into
listing your features or capabilities unless they actually asked what you can
do. Match the weight of your reply to the weight of the question.
"""

_MAX_TOOL_ROUNDS = 3  # safety cap against a runaway tool-call loop

# Defense in depth alongside the system prompt's "no markdown/URLs" instruction
# -- confirmed Claude doesn't reliably follow that instruction alone (a crisis
# reply came back with **bold** hotline labels and a raw URL, both read aloud
# literally by TTS). Strips syntax rather than dropping content.
_MARKDOWN_BOLD_ITALIC_RE = re.compile(r"\*\*?([^*]+)\*\*?")
_MARKDOWN_HEADER_RE = re.compile(r"^#+\s*", re.MULTILINE)
_MARKDOWN_BULLET_RE = re.compile(r"^[ \t]*[\*\-]\s+", re.MULTILINE)
_MARKDOWN_BACKTICK_RE = re.compile(r"`([^`]*)`")
_URL_RE = re.compile(r"https?://\S+")
# Confirmed in Hebrew replies: an emoji left in the text gets read aloud as
# its name (e.g. TTS saying "smiling face") instead of being skipped.
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F700-\U0001FAFF"  # alchemical/extended-A/supplemental symbols
    "\U00002600-\U000026FF"  # misc symbols
    "\U00002700-\U000027BF"  # dingbats
    "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
    "\U00002300-\U000023FF"  # misc technical (includes some clock/hourglass glyphs)
    "\U0000200D"  # zero-width joiner (compound emoji)
    "\U0000FE0F"  # variation selector-16 (emoji presentation)
    "]+",
    flags=re.UNICODE,
)


def _strip_voice_unfriendly_formatting(text: str) -> str:
    text = _URL_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    text = _MARKDOWN_BOLD_ITALIC_RE.sub(r"\1", text)
    text = _MARKDOWN_HEADER_RE.sub("", text)
    text = _MARKDOWN_BULLET_RE.sub("", text)
    text = _MARKDOWN_BACKTICK_RE.sub(r"\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()


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
    language_name = LANGUAGE_NAMES[language]
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
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": execute_tool(block.name, language, block.input),
                }
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
    reply = _strip_voice_unfriendly_formatting(reply)
    messages.append({"role": "assistant", "content": response.content})
    return reply, messages
