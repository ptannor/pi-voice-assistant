"""Conversational responses via Anthropic's Claude API."""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from .config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    HOUSEHOLD_LOCATION,
    HOUSEHOLD_NEARBY_AREAS,
    HOUSEHOLD_TIMEZONE,
)
from .language import LANGUAGE_NAMES
from .memory import memory_prompt_block
from .tools import TOOLS, execute_tool

_NEARBY_AREAS_CLAUSE = (
    f" Nearby areas -- {HOUSEHOLD_NEARBY_AREAS} -- are close enough to treat "
    "as local too, not a different region; a result about one of them is not "
    "a mismatch just because the city name isn't the household's own."
    if HOUSEHOLD_NEARBY_AREAS
    else ""
)

_LOCATION_PROMPT_LINE = (
    f"\nThe household is in {HOUSEHOLD_LOCATION}.{_NEARBY_AREAS_CLAUSE} Default "
    "to answers, recommendations, and services relevant there rather than "
    "assuming the US or anywhere else. If you're not fully certain of a "
    "specific, current fact tied to this location (e.g. a phone number, "
    "address, or hours), use the web_search tool to check it rather than "
    "reciting one from memory.\n"
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
actually have, unless a tool result actually states that exact detail --
this applies broadly, to any precise-sounding number or fact (an exact
showtime, theater number, running time, temperature, forecast, price,
zmanim, parsha, anything), not just the examples given here. A web search
snippet is often a vague description, not exact figures -- if you don't
have the precise number, say plainly which part you know and which you
don't (e.g. "it's playing today, but I don't have the exact time") instead
of stating something specific-sounding that you're inferring or guessing.

You will sometimes get a transcribed question with a garbled or
unfamiliar-sounding name (speech-to-text mishears names it doesn't
recognize, especially in Hebrew). If a search for that name turns up
something with a very similar-sounding real name, treat that as very
likely the same thing the user meant, rather than concluding they're two
different things and inventing an explanation for the mismatch (e.g. don't
assert that a garbled name refers to a different, unverified place/thing
just because the spelling doesn't match exactly).

When the user shares something worth remembering for future conversations --
names, allergies, recurring preferences, house rules -- use the remember
tool to save it, without making a big deal of it (a brief acknowledgment is
enough). If they ask you to forget something, use the forget tool. Don't
save something as a permanent memory just because it came up once in
passing; save it when it's clearly meant to stick.

There's also a household reference library (recipes, family member details,
birthdays, school/activity schedules, and more) too big to keep in context by
default -- use the search_household_info tool to look something up from it
whenever a question sounds like it could be answered from that kind of
detail, rather than assuming you don't have it.

If a web_search only gets you a partial answer (e.g. a list of movies but not
showtimes), don't tell the user to go check a website or app themselves --
you have the tool to look it up, so either search again with a more specific
query, or if you're missing something only the user knows (which movie,
which day), ask that one question and then search once you have it. Treat a
suspiciously short or generic-looking list as incomplete, not final -- e.g.
for "what's playing at a cinema" a handful of genre-sounding words is a sign
you're reading a noisy snippet, not the real lineup (a real multiplex usually
has ~8-12 films running). Re-search with different phrasing rather than
reporting that short list as the answer. But finishing the task never means
inventing a specific fact your search results don't actually contain -- see
above.

Don't habitually end every reply with "anything else I can help with?" out of
politeness -- only ask a follow-up question when you genuinely need more
information to help with something the user is mid-way through.

For casual, playful, or rhetorical questions (small talk, "do you love me?",
jokes, greetings), give ONE short, warm sentence back -- don't pivot into
listing your features or capabilities unless they actually asked what you can
do. Match the weight of your reply to the weight of the question.
"""

_MAX_TOOL_ROUNDS = 4  # safety cap against a runaway tool-call loop -- a real
# answer sometimes needs 2-3 searches (broad query, then a more specific
# retry), so this leaves a bit of headroom before the round-cap fallback below

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


def _current_datetime_line() -> str:
    # An LLM has no built-in clock -- without this, it has no way to know
    # what "today"/"tomorrow" even means, let alone sanity-check a claim like
    # a showtime against what time it actually is (confirmed: asked to
    # explain a "10 a.m. today" showtime at 11 p.m., Claude admitted it had
    # no idea what the current date or time was).
    now = datetime.now(ZoneInfo(HOUSEHOLD_TIMEZONE))
    return (
        f"\nRight now it's {now.strftime('%A, %B %-d, %Y, %-I:%M %p')} "
        f"({HOUSEHOLD_TIMEZONE}). Use this for anything relative (today, "
        "tomorrow, in an hour) and to sanity-check any date or time before "
        "stating it.\n"
    )


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
    # Computed fresh per call (not baked into the static SYSTEM_PROMPT, which
    # is built once at import time) so a long-running daemon always gives
    # Claude the actual current time, and a fact remembered mid-conversation
    # is visible on the very next turn, not just from process restart.
    system_prompt = SYSTEM_PROMPT + _current_datetime_line() + memory_prompt_block()
    messages = (history or []) + [
        {"role": "user", "content": f"[The user spoke in {language_name}] {user_text}"}
    ]

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=300, system=system_prompt, tools=TOOLS, messages=messages
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
                model=CLAUDE_MODEL, max_tokens=300, system=system_prompt, tools=TOOLS, messages=messages
            )

        if response.stop_reason == "tool_use":
            # Hit the round cap while Claude still wanted to call another
            # tool. That response's text (if any) is just in-progress
            # "here's what I'll try next" narration, not a real answer --
            # confirmed: text like "Let me search more directly for..." got
            # read aloud verbatim once. Discard it and force one final,
            # tool-free turn on the same history so Claude commits to its
            # best answer from what it's already gathered.
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=300,
                system=system_prompt,
                tools=TOOLS,
                tool_choice={"type": "none"},
                messages=messages,
            )
    except Exception as exc:
        raise BrainError(f"Claude request failed: {exc}") from exc

    reply = "".join(block.text for block in response.content if block.type == "text").strip()
    if not reply:
        # Confirmed possible (though rare) when tool_choice=none is forced
        # above with little else to go on -- silence is worse than an
        # explicit, honest "couldn't find it".
        reply = "לא הצלחתי למצוא תשובה ברורה לזה, סליחה." if language == "he" else "Sorry, I couldn't find a clear answer to that."
    reply = _strip_voice_unfriendly_formatting(reply)
    messages.append({"role": "assistant", "content": response.content})
    return reply, messages
