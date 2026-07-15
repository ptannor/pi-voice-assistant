"""Conversational responses via Anthropic's Claude API."""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import anthropic

from .config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    HOUSEHOLD_FAMILY_NAMES_EN,
    HOUSEHOLD_FAMILY_NAMES_HE,
    HOUSEHOLD_LOCATION,
    HOUSEHOLD_NEARBY_AREAS,
    HOUSEHOLD_TIMEZONE,
)
from .language import LANGUAGE_NAMES
from .memory import memory_prompt_block
from .mode import is_funny_voice_enabled
from .tools import execute_tool, get_tools_for_language

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

# The same config values brain/stt.py uses to bias transcription -- also
# telling Claude who these people actually are, not just how to spell them.
# Confirmed a gap: without this, Claude had no idea these were the family
# even though STT was already recognizing the names correctly.
_family_name_parts = [n for n in (HOUSEHOLD_FAMILY_NAMES_EN, HOUSEHOLD_FAMILY_NAMES_HE) if n]
_FAMILY_PROMPT_LINE = (
    f"\nThe household's family members are: {' / '.join(_family_name_parts)} "
    "(same people, in English and Hebrew forms). Recognize these as family "
    "when they come up, not as unfamiliar words -- but that's genuinely all "
    "you know about them. You do NOT know their ages, or who's a parent, "
    "child, sibling, or spouse -- don't state or imply any of that unless a "
    "tool result or a remembered fact actually confirms it. Concretely: "
    "\"X is one of the children\" or \"X is Y's sibling\" are both things you "
    "must NOT say unless you're told so elsewhere -- the correct answer to "
    "\"who is X\" is just \"X is family, I don't know more than that,\" in "
    "whichever language you're replying in. This applies the same way in "
    "Hebrew as in English.\n"
    if _family_name_parts
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

When giving a word from another language (e.g. a Hebrew word while replying
in English), say it only once, written the way it should be pronounced --
don't also give its spelling in the other script or a separate
transliteration. Reading it twice sounds like a stutter, since both forms
are pronounced the same way out loud (e.g. say "A monkey in Hebrew is kof,"
not "...is kof -- קוף").

For any nontrivial arithmetic (multi-digit multiplication, multiple steps,
an exponent) use the calculate tool instead of computing it mentally --
confirmed necessary: asked to compute a multi-step expression, the answer
was confidently wrong.

If the user asks to switch to "funny voice mode" (or a silly/funny voice),
or back to "regular"/"normal" voice, call the set_voice_mode tool with
mode="funny" or mode="regular" accordingly, then reply briefly and
playfully confirming the switch -- in the same language the user just used,
same as any other reply. You'll be told below whether funny voice mode is
currently on -- that's the source of truth, not anything said earlier in
this conversation.
{_LOCATION_PROMPT_LINE}{_FAMILY_PROMPT_LINE}
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

When the user asks to play a song, podcast, show, or episode (e.g., using "תנגן", "תשמיע", "תשים פודקאסט", "play podcast", "play"):
1. If the user's request appears to contain lyrics from a song (e.g., they ask for "the song that says [lyrics]"), first call the web_search tool to look up the song title and artist based on those lyrics.
2. Correct any obvious spelling mistakes, typos, or speech-to-text garbling in the query. Call the search_music_hebrew (or search_music_english) tool with this query to get the top candidate tracks/episodes/shows from Spotify.
3. Inspect the candidates (which contain name, artist, type ("track", "episode", or "show"), popularity score, and URI):
   - Rely on popularity, relevance, and the household's musical taste / favorite artists (which might be noted in the household memories, e.g., Hanan Ben Ari, Kfir Tsafrir, Ishay Ribo, Billy Joel, Stilla, Ness, etc.) to identify the most likely match.
   - If there is a single outstanding match, call play_music_hebrew (or play_music_english) immediately with that item's URI.
   - If there are two or three prominent/equally likely candidates, stop and ask the user a very brief clarification question listing the 2 or 3 options using the minimum number of words possible (e.g., in Hebrew: "הפודקאסט של שיר אחד או של חיות כיס?").
   - Once they clarify, call play_music_hebrew (or play_music_english) with the correct item's URI.
Never ask for clarification if there is a clear winner; keep the flow fast and immediate. If a search fails or no matching songs/shows are found, explain this briefly in the user's language (e.g., in Hebrew, never in English). If a music playback tool returns "status: error_no_active_device", tell the user in Hebrew that they need to open Spotify on a device first before you can play music (e.g., "פתח בבקשה את ספוטיפיי במכשיר כלשהו תחילה").

When the user asks to resume, resume playing, or continue playing paused music (e.g., using "תמשיך", "להמשיך", "resume", "continue", "play"), call play_music_hebrew (or play_music_english) with the query "resume" to continue the track from where it was paused.

When the user asks to seek, skip, skip forward, skip backward, fast forward, or rewind in the current song (e.g., "דלג 30 שניות קדימה", "תחזיר דקה אחורה", "fast forward 20 seconds", "דלג קדימה"), determine the number of seconds to shift (use a positive number of seconds to skip forward, or a negative number to go backward) and call the seek_music_hebrew (or seek_music_english) tool.

When the user asks to skip the entire song, skip this song, go to the next song/track, or go back to the previous song/track (e.g., "דלג לשיר הבא", "דלג על השיר", "השיר הבא", "תחזור לשיר הקודם", "הקודם", "skip track"), determine the direction ("next" or "previous") and call the skip_track_hebrew (or skip_track_english) tool.

If the user asks to stop, cancel, or pause the music or timer (e.g., using "עצור", "עצרי", "stop", "בטל את הטיימר"), call the appropriate tool, and reply with an empty text response (do not say "עצרתי" or any verbal confirmation).

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

# Reused across calls instead of constructing a fresh client (and its TLS
# handshake) on every single turn.
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise BrainError("ANTHROPIC_API_KEY not set -- add it to .env")
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client

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


def _funny_voice_prompt_line() -> str:
    # Computed fresh per call, same as _current_datetime_line() -- the mode
    # flag lives in mode.py (in-process, not conversation history), so a
    # brand-new conversation started after a prior one turned funny voice on
    # still needs to be told that here, not left to guess from its own memory.
    if not is_funny_voice_enabled():
        return ""
    return (
        "\nFunny voice mode is currently ON. At the very end of every single "
        "reply, after everything else you'd otherwise say, add the exact "
        "phrase \"אני קוקו על כל הראש, אמא אפשר פסטה בולונז\" word for word, "
        "unchanged, regardless of what the question was or which language "
        "you replied in.\n"
    )


def _timer_prompt_line() -> str:
    try:
        from . import timer
        if timer.is_timer_active():
            return "\nThere is currently an active background countdown timer running.\n"
    except Exception:
        pass
    return ""


def _get_empty_reply_fallback(language: str, timeline: list[tuple[str, float]]) -> str:
    # Find if any tool was executed in this turn
    tool_stages = [stage for stage, _ in timeline if stage.startswith("tool:")]
    if not tool_stages:
        return "לא הצלחתי למצוא תשובה ברורה לזה, סליחה." if language == "he" else "Sorry, I couldn't find a clear answer to that."
    
    # Get the last tool stage name
    last_tool = tool_stages[-1].replace("tool:", "")
    if "stop" in last_tool or "cancel" in last_tool or "play_music" in last_tool or "seek_music" in last_tool:
        return ""
    elif "set_timer" in last_tool:
        return "הטיימר הוגדר." if language == "he" else "Timer set."
    else:
        return "בוצע." if language == "he" else "Done."


def ask(
    user_text: str,
    language: str,
    history: list[dict] | None = None,
    on_tool_call: Callable[[], None] | None = None,
) -> tuple[str, list[dict], list[tuple[str, float]]]:
    """`language` is "he" or "en" (see brain/language.py).

    `history` is prior turns of the same conversation (None/empty for a new
    one); returns (reply_text, updated_history, timeline) so callers can keep
    passing the running history back in for follow-up turns within one
    session. `timeline` is a list of (stage, seconds) pairs in call order --
    one entry per Claude API round-trip ("claude") and one per tool
    execution ("tool:<name>") -- for breaking down where a turn's latency
    actually went (e.g. Claude thinking time vs. a slow web_search) instead
    of only knowing the total.

    `on_tool_call`, if given, fires once, the first time this turn needs a
    tool round -- lets the caller play an acknowledgment sound while a slow
    tool call (e.g. web_search) is in flight, since that's the dominant cost
    on most factual questions and the assistant would otherwise sit silent
    for several seconds. Best-effort: an exception from it is swallowed
    rather than failing the whole turn over a missed sound cue.
    """
    client = _get_client()
    timeline: list[tuple[str, float]] = []

    def _timed(label, fn, *args, **kwargs):
        t0 = time.monotonic()
        result = fn(*args, **kwargs)
        timeline.append((label, time.monotonic() - t0))
        return result
    language_name = LANGUAGE_NAMES[language]

    lang_tools = get_tools_for_language(language)
    lang_tools_cached = [*lang_tools[:-1], {**lang_tools[-1], "cache_control": {"type": "ephemeral"}}] if lang_tools else lang_tools

    # The datetime/memory block is computed fresh per call (a long-running
    # daemon always needs the actual current time, and a fact remembered
    # mid-conversation must be visible on the very next turn) but kept as a
    # separate, uncached system block so it doesn't bust the cache on the
    # much larger, truly static SYSTEM_PROMPT text below -- that prompt is
    # ~1,500 tokens resent on every single call otherwise.
    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": _current_datetime_line() + memory_prompt_block() + _funny_voice_prompt_line() + _timer_prompt_line()},
    ]

    messages = (history or []) + [
        {"role": "user", "content": f"[The user spoke in {language_name}] {user_text}"}
    ]

    try:
        response = _timed(
            "claude",
            client.messages.create,
            model=CLAUDE_MODEL, max_tokens=300, system=system_blocks, tools=lang_tools_cached, messages=messages,
        )
        if response.stop_reason == "tool_use" and on_tool_call is not None:
            try:
                on_tool_call()
            except Exception:
                pass
        rounds = 0
        while response.stop_reason == "tool_use" and rounds < _MAX_TOOL_ROUNDS:
            rounds += 1
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _timed(f"tool:{block.name}", execute_tool, block.name, language, block.input),
                }
                for block in response.content
                if block.type == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
            # Recompute system blocks in case a tool (like set_voice_mode or remember) updated the state
            system_blocks = [
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": _current_datetime_line() + memory_prompt_block() + _funny_voice_prompt_line() + _timer_prompt_line()},
            ]
            response = _timed(
                "claude",
                client.messages.create,
                model=CLAUDE_MODEL, max_tokens=300, system=system_blocks, tools=lang_tools_cached, messages=messages,
            )

        if response.stop_reason == "tool_use":
            # Hit the round cap while Claude still wanted to call another
            # tool. That response's text (if any) is just in-progress
            # "here's what I'll try next" narration, not a real answer --
            # confirmed: text like "Let me search more directly for..." got
            # read aloud verbatim once. Discard it and force one final,
            # tool-free turn on the same history so Claude commits to its
            # best answer from what it's already gathered.
            system_blocks = [
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": _current_datetime_line() + memory_prompt_block() + _funny_voice_prompt_line() + _timer_prompt_line()},
            ]
            response = _timed(
                "claude_forced_final",
                client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=300,
                system=system_blocks,
                tools=lang_tools_cached,
                tool_choice={"type": "none"},
                messages=messages,
            )
    except Exception as exc:
        raise BrainError(f"Claude request failed: {exc}") from exc

    reply = "".join(block.text for block in response.content if block.type == "text").strip()
    if not reply:
        reply = _get_empty_reply_fallback(language, timeline)

    # Force silent replies for stop/cancel/play tools as requested by user ("you don't need to say עצרתי" or "בוצע")
    tool_stages = [stage for stage, _ in timeline if stage.startswith("tool:")]
    if tool_stages:
        last_tool = tool_stages[-1].replace("tool:", "")
        if "stop" in last_tool or "cancel" in last_tool:
            reply = ""
        elif "play_music" in last_tool or "seek_music" in last_tool or "skip_track" in last_tool or "stop_music" in last_tool:
            # If the playback tool successfully played, resumed, seeked, skipped, or stopped, force the response to be completely silent
            is_silent_success = False
            for msg in reversed(messages):
                if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    for content in msg["content"]:
                        if content.get("type") == "tool_result":
                            res_str = str(content.get("content"))
                            if "status: playing" in res_str or "status: resumed" in res_str or "status: seeked" in res_str or "status: skipped" in res_str or "status: stopped" in res_str:
                                is_silent_success = True
                                break
                if is_silent_success:
                    break

            if is_silent_success:
                reply = ""
            else:
                clean_reply = reply.replace("!", "").replace(".", "").replace(",", "").strip().lower()
                if (clean_reply in ("בוצע", "עצרתי", "done", "stopped", "resumed", "resuming", "music resumed", "ממשיך", "ממשיך לנגן") or
                    any(w in clean_reply for w in ("seeked", "skipped", "דילגתי", "חזרתי", "לדלג"))):
                    reply = ""

    reply = _strip_voice_unfriendly_formatting(reply)
    messages.append({"role": "assistant", "content": response.content})
    return reply, messages, timeline
