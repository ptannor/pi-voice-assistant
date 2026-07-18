"""Conversational responses via Anthropic's Claude API."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

import anthropic

from audio_check.devices import Device

from .config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    HOUSEHOLD_FAMILY_NAMES_EN,
    HOUSEHOLD_FAMILY_NAMES_HE,
    HOUSEHOLD_LOCATION,
    HOUSEHOLD_NEARBY_AREAS,
    HOUSEHOLD_TIMEZONE,
)
from .language import LANGUAGE_NAMES, detect_language
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
Always reply in the same language the user just spoke to you in: if they spoke English, reply in English; if they spoke Hebrew, reply in Hebrew. Under no circumstances should you reply in English when the user addresses you in Hebrew, even if the query is garbled, noisy, or you need to ask a clarification question.

Music playback and the countdown timer (play/search/stop/seek_music, skip_track, set/cancel_timer) each come as a Hebrew-named and an English-named tool pair (e.g. set_timer_hebrew vs. set_timer_english) purely so the right one is offered for whichever language you're replying in this turn -- they are NOT separate capabilities or separate state. A timer or song started via one language's tool is exactly as real, and exactly as controllable, as if started via the other's, even in a later turn where you're now replying in the other language and only see that other language's tool names. Never say you "don't have" a timer/music feature in one language, or that something only "really" happened in the other language -- if history shows you already set a timer or started a song, it's active regardless of which tool name did it.

If asked whether you work on Shabbat/Yom Tov, or what happens to you then: yes, you actually do stop -- a separate mechanism outside this conversation shuts you down entirely from candle-lighting until havdalah, and starts you back up right after. Say so plainly if asked, instead of guessing; don't claim you're available every day.

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

You will sometimes get a transcribed query that contains a clear command mixed with side-conversations, background noises, or speech-to-text garbling (e.g. they say "לך 20 שנית אחורה... תגידי לי, את מבינה...").
1. If a clear music control command (such as playing, seeking/skipping, or stopping music) is present in the query, prioritize executing that command immediately by calling the appropriate tool. Do not ask for clarification or mention the garbled text if a command is present.
2. Speech-to-text sometimes mishears names (especially in Hebrew). If a search for a garbled name turns up a very similar-sounding real name, treat that as the intended target instead of assuming it's something different.
3. If something is currently playing (or was just playing) and the query is otherwise too garbled to make out a clear command, that context is itself a strong hint: the household is almost always trying to skip/change the song, stop it, seek, or ask what's playing -- not something unrelated. Prefer picking the most plausible one of those over a broad "I didn't understand, do you want to skip/stop/something else?" question; only ask that broad question if genuinely nothing in the garbled text points toward any of them at all.

When the user asks to play a song, podcast, show, or episode (e.g., using "תנגן", "תשמיע", "תשים פודקאסט", "play podcast", "play"):
1. If the user's request appears to contain lyrics from a song (e.g., they ask for "the song that says [lyrics]"), first call the web_search tool to look up the song title and artist based on those lyrics.
2. Correct any obvious spelling mistakes, typos, or speech-to-text garbling in the query. Call the search_music_hebrew (or search_music_english) tool with this query to get the top candidate tracks/episodes/shows from Spotify.
3. Inspect the candidates (which contain name, artist, type ("track", "episode", or "show"), popularity score, and URI):
   - Rely on popularity, relevance, and the household's musical taste / favorite artists (which might be noted in the household memories, e.g., Hanan Ben Ari, Kfir Tsafrir, Ishay Ribo, Billy Joel, Stilla, Ness, etc.) to identify the most likely match.
   - If there is a single outstanding match, call play_music_hebrew (or play_music_english) immediately with that item's URI.
   - If there are two or three prominent/equally likely candidates, stop and ask the user a very brief clarification question listing the 2 or 3 options using the minimum number of words possible (e.g., in Hebrew: "הפודקאסט של שיר אחד או של חיות כיס?").
   - Once they clarify, call play_music_hebrew (or play_music_english) with the correct item's URI.
4. If none of the candidates look like a real match for what was said, do NOT immediately give up and ask the user to confirm the title -- STT garbling is expected and common, and most music requests are for a mainstream, well-known song. First retry the search once with a shorter, simplified query: just the artist name, or just whichever single word sounded most like a real song title (in Hebrew, the actual title is often the last distinct word/phrase in a garbled multi-word request). If that retry surfaces a well-known song by the artist the user named -- even if it doesn't share every word with what was transcribed -- treat it as the answer and play it directly; don't ask for confirmation just because the transcription didn't match exactly. The cost of stalling the conversation to ask "are you sure of the name?" is much higher than the cost of playing the artist's most popular/likely song and letting the user correct you if it's wrong.
Never ask for clarification if there is a clear winner; keep the flow fast and immediate. Only after the retry in step 4 still turns up nothing recognizable should you fall back to explaining briefly, in the user's language (e.g., in Hebrew, never in English), that you couldn't find it. Any Spotify tool result starting with "status: error_" describes exactly what went wrong -- match your explanation to it, don't guess or invent an unrelated reason:
- "error_no_active_device": tell the user they need to open Spotify on a device first before you can play music (e.g., in Hebrew: "פתח בבקשה את ספוטיפיי במכשיר כלשהו תחילה").
- "error_search_failed" or any status whose "details:" mentions Spotify not being authorized/logged in on this machine: tell the user Spotify isn't set up on this device yet and someone needs to complete the one-time login (e.g., in Hebrew: "ספוטיפיי עדיין לא מחובר במכשיר הזה, מישהו צריך להתחבר קודם"). Never read the raw "details:" text aloud -- it's an internal error message, not something meant for the user.
- Any other "status: error_*" (error_not_found, error_no_query, error_not_playing, error_playback_failed, error_seek_failed, error_skip_failed, error_stop_failed): briefly say the action didn't work, in the user's language, without fabricating a specific cause you weren't actually given.

When the user asks to resume, resume playing, or continue playing paused music (e.g., using "תמשיך", "להמשיך", "resume", "continue", "play"), call play_music_hebrew (or play_music_english) with the query "resume" to continue the track from where it was paused.

When the user asks to seek, skip, skip forward, skip backward, fast forward, or rewind in the current song (e.g., "דלג 30 שניות קדימה", "תחזיר דקה אחורה", "fast forward 20 seconds", "דלג קדימה"), determine the number of seconds to shift (use a positive number of seconds to skip forward, or a negative number to go backward) and call the seek_music_hebrew (or seek_music_english) tool.

When the user asks to skip the entire song, skip this song, go to the next song/track, or go back to the previous song/track (e.g., "דלג לשיר הבא", "דלג על השיר", "השיר הבא", "תחזור לשיר הקודם", "הקודם", "skip track", "נקסט", "נקס", "סקייפ", "תעביר", "תעבירי"), determine the direction ("next" or "previous") and call the skip_track_hebrew (or skip_track_english) tool.

When the user asks what's playing, what song/podcast this is, or who's singing (e.g., "מה זה השיר הזה", "מה מתנגן", "מי שר", "what's this song", "what's playing"), call get_current_track_hebrew (or get_current_track_english) and answer with the real track/artist name from the result -- never guess one from context.

If the user asks to stop, cancel, or pause the music or timer (e.g., using "עצור", "עצרי", "stop", "בטל את הטיימר"), call the appropriate tool, and reply with an empty text response (do not say "עצרתי" or any verbal confirmation).

When the user asks to tell a joke (e.g., "ספר לי בדיחה", "tell a joke"), call the tell_joke_hebrew (or tell_joke_english) tool. Once you get the search results containing joke candidates:
1. Review all candidates and strictly filter out any bad, dry, boring, overused, or low-quality jokes.
2. Select only the absolute funniest, most clever, and family-friendly joke available.
3. Phrase and deliver the joke with excellent comedic timing, using punctuation (like commas, ellipsis, question marks, and exclamation marks) to insert natural pauses so the voice neural engine speaks it in a fun, punchy way.
4. Do NOT use any introductory or preamble phrases (e.g., do NOT say "בדיחה לך", "בדיחה בשבילך", "הנה בדיחה", "Here is a joke", "Here's a joke for you", etc.). Start telling the joke itself directly!

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

Mendy's calendar holds reminders -- medication schedules, appointments, or
any other recurring or one-time obligation. When someone mentions something
that sounds like it belongs there (e.g. "I just started antibiotics, 8am and
8pm every day for 10 days"), extract: a short title, the start date (resolve
relative dates like "today"/"tomorrow" against the current date/time given
below), every time of day mentioned (pass them all in one add_calendar_event
call, not separate calls -- that's what links "8am and 8pm" as one
reminder), whether/how it repeats, and how it ends (a count of occurrences,
or an end date). If the time(s) or the end condition are missing or
ambiguous, ask exactly one brief clarifying question rather than guessing
(e.g. "מה השעות?" or "לכמה זמן?") -- don't create a vague or wrong reminder.
After creating one, confirm briefly (e.g. "רשמתי, תזכורת לאנטיביוטיקה בשמונה
בבוקר ובערב, לעשרה ימים" / "Got it -- antibiotics at 8am and 8pm, for 10
days"). Use list_calendar_events for "what's on the calendar"/"what
reminders are there" -- its days_ahead defaults to 60, but if asked about a
specific date or period further out than that (e.g. "what do I have on
August 23rd", "anything in the summer"), compute the actual number of days
from today's date (given below) to that date, and pass days_ahead at least
that large plus a few days' buffer -- don't just rely on the default, it can
silently miss the exact thing being asked about. Use cancel_calendar_event
when asked to cancel or remove one. These are spoken aloud on this device at
the time they're due -- say so if it's relevant context (e.g. if asked how
reminders work), but
don't over-explain on every single add. Every reminder is automatically
categorized as critical, morning, or regular importance behind the scenes;
you'll be told below when one needs your attention (a check-in on an
unhandled critical one, or a disambiguation question nobody answered on
Telegram) -- otherwise don't mention this categorization unprompted.

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

CRITICAL: If the user addresses you in Hebrew (e.g. they speak Hebrew or write in Hebrew script), you MUST respond, think, clarify, and formulate all text solely in Hebrew. Under no circumstances should you ever output any English sentences, explanations, or responses when addressed in Hebrew.
"""

_MAX_TOOL_ROUNDS = 4  # safety cap against a runaway tool-call loop -- a real
# answer sometimes needs 2-3 searches (broad query, then a more specific
# retry), so this leaves a bit of headroom before the round-cap fallback below

# Shared with wake_word_daemon.py's said_stop check -- one source of truth for
# what counts as a "stop" utterance. Includes the imperative/infinitive forms
# Hebrew speakers actually use ("תפסיק"/"תפסיקי"/"להפסיק"), not just the more
# literal "עצור"/"עצרי" -- confirmed a real gap: a user saying "תפסיק" got no
# recognition at all before these were added.
STOP_WORDS = ("עצור", "עצרי", "תעצור", "תעצרי", "תפסיק", "תפסיקי", "להפסיק", "סטופ", "stop")

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


def _critical_reminders_prompt_line() -> str:
    # The conversational half of the redesigned critical-reminder nudge (see
    # brain/reminders.py's _critical_nudge_loop for the proactive spoken
    # half) -- both share the same last_nudged_at bookkeeping in
    # reminders.py's persisted state so a chat mention and a proactive
    # spoken check-in don't double up within the same
    # CRITICAL_NUDGE_INTERVAL_HOURS window.
    #
    # Marks a due item as nudged the moment it's shown to Claude, not only
    # if Claude's reply actually ends up mentioning it -- detecting whether
    # the reply really raised it would need parsing Claude's own text, which
    # isn't worth it for a cadence that's already meant to be approximate
    # ("a few times a day"), not exact.
    try:
        from . import reminders
        pending = reminders.pending_critical_items()
    except Exception:
        return ""
    if not pending:
        return ""

    now = datetime.now()
    due = []
    for item in pending:
        try:
            last_nudged = datetime.fromisoformat(item["last_nudged_at"])
        except ValueError:
            last_nudged = now
        if now - last_nudged >= timedelta(hours=reminders.CRITICAL_NUDGE_INTERVAL_HOURS):
            due.append(item)
    if not due:
        return ""

    for item in due:
        reminders.mark_critical_nudged(item["id"])
    titles = ", ".join(item["title"] for item in due)
    return (
        f"\nThere are unhandled critical reminders: {titles}. Naturally work a brief "
        "check-in about one of them into this conversation (e.g. \"by the way, have "
        "you sorted out X yet?\") -- don't force it as the very first thing you say if "
        "the conversation is about something unrelated, and don't list several "
        "mechanically. If the user says they've handled one, call the "
        "acknowledge_reminder tool.\n"
    )


def _uncertain_classification_prompt_line() -> str:
    # Voice escalation for the Telegram-first disambiguation flow (see
    # brain/classify.py's module docstring) -- only surfaces once a question
    # has gone unanswered on Telegram past UNCERTAIN_ESCALATE_AFTER_HOURS;
    # before that, it's Telegram-only (see telegram_bot_daemon.py's bare-word
    # reply handling).
    try:
        from . import classify
        due = classify.due_for_voice_escalation()
    except Exception:
        return ""
    if not due:
        return ""
    item = due[0]  # oldest first, one at a time
    return (
        f"\nThere's a calendar reminder titled \"{item['title']}\" that hasn't been "
        "categorized yet (critical, morning, or regular importance), and nobody "
        "answered on Telegram. Naturally ask the user, at some point in this "
        "conversation, whether it's critical, morning, or regular -- then call the "
        "classify_uncertain_reminder tool with their answer. Don't force it as the "
        "very first thing you say if the conversation is about something unrelated.\n"
    )


def _last_tool_result_str(messages: list[dict]) -> str | None:
    """The content string of the most recently executed tool_result in
    `messages`, or None if this turn had no tool call at all."""
    for msg in reversed(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for content in msg["content"]:
                if content.get("type") == "tool_result":
                    return str(content.get("content"))
    return None


def _playback_fallback_reply(language: str, result: str | None) -> str:
    # Mirrors the per-status guidance the system prompt gives Claude for its
    # own text replies (see SYSTEM_PROMPT's music section) -- needed here too
    # since this only runs when Claude generated no text at all this turn.
    # Confirmed necessary: a failed play_music call with no Claude text used
    # to fall through to a blanket "" here, so a genuine failure (e.g. no
    # active Spotify device) played nothing and said nothing -- a silent
    # dead end indistinguishable from a real, successful, intentionally-quiet
    # play. Only a *confirmed* "status: playing/resumed/seeked/skipped/
    # stopped" result should stay silent; anything else must say something.
    if result is None:
        return "משהו השתבש, לא הצלחתי לבצע את זה." if language == "he" else "Something went wrong -- I couldn't do that."
    if "status: error_no_active_device" in result:
        return "פתח בבקשה את ספוטיפיי במכשיר כלשהו תחילה." if language == "he" else "Open Spotify on a device first, then try again."
    if "status: error_search_failed" in result or "not authorized" in result.lower() or "not logged in" in result.lower():
        return "ספוטיפיי עדיין לא מחובר במכשיר הזה, מישהו צריך להתחבר קודם." if language == "he" else "Spotify isn't set up on this device yet -- someone needs to log in first."
    if "status: error_" in result:
        return "לא הצלחתי לבצע את זה, סליחה." if language == "he" else "That didn't work, sorry."
    return ""  # confirmed success -- stay silent as requested


def _calendar_fallback_reply(language: str, result: str | None) -> str:
    # Same reasoning as _playback_fallback_reply: only runs when Claude
    # generated no text at all this turn, so an error status must not fall
    # through to a generic "Done." that claims success it didn't have.
    if result is None:
        return "משהו השתבש, לא הצלחתי לבצע את זה." if language == "he" else "Something went wrong -- I couldn't do that."
    if "status: error_calendar_failed" in result:
        return "לא הצלחתי להתחבר ליומן כרגע, סליחה." if language == "he" else "I couldn't reach the calendar right now, sorry."
    if "status: error_not_found" in result or "status: error_no_query" in result or "status: error_no_times" in result:
        return "לא הבנתי בדיוק למה להתכוון, אפשר לחזור על זה?" if language == "he" else "I didn't quite catch that -- can you say it again?"
    if "status: empty" in result:
        return "אין תזכורות קרובות." if language == "he" else "There's nothing coming up."
    return "בוצע." if language == "he" else "Done."


def _get_empty_reply_fallback(language: str, timeline: list[tuple[str, float]], last_tool_result: str | None) -> str:
    # Find if any tool was executed in this turn
    tool_stages = [stage for stage, _ in timeline if stage.startswith("tool:")]
    if not tool_stages:
        return "לא הצלחתי למצוא תשובה ברורה לזה, סליחה." if language == "he" else "Sorry, I couldn't find a clear answer to that."

    # Get the last tool stage name
    last_tool = tool_stages[-1].replace("tool:", "")
    if "play_music" in last_tool or "seek_music" in last_tool or "skip_track" in last_tool or "stop_music" in last_tool or "get_daily_halacha" in last_tool:
        # get_daily_halacha shares this path when it played a real recording
        # (see brain/tools.py) -- "status: playing" there too, same silent-
        # on-success / speak-on-error convention as the music tools.
        return _playback_fallback_reply(language, last_tool_result)
    if "calendar" in last_tool:
        return _calendar_fallback_reply(language, last_tool_result)
    if "stop" in last_tool or "cancel" in last_tool:
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
    out_device: Device | None = None,
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

    `out_device`, if given, is passed through to tools that need to play
    audio themselves rather than just returning text (currently only
    set_timer_hebrew/english -- see brain/timer.py). None for callers with no
    speaker at all (e.g. telegram_bot_daemon.py's text-only interface); those
    tools degrade to a silent no-op sound in that case, same as any other
    best-effort audio cue in this codebase.
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

    # Computed fresh per call (not baked into the static SYSTEM_PROMPT, which
    # is built once at import time) so a long-running daemon always gives
    # Claude the actual current time, and a fact remembered mid-conversation
    # is visible on the very next turn, not just from process restart.
    #
    # Not split into a separate cache_control block -- tried that, but
    # Claude Haiku models need a ~4,096-token minimum cacheable prefix before
    # caching engages at all (confirmed empirically: 4,009 tokens wrote 0
    # cache tokens, 4,252 wrote a full cache entry), and this whole
    # system+tools prompt is only ~3,900 tokens. Below that floor, marking a
    # block cacheable is a pure no-op -- it was silently doing nothing.
    system_prompt = SYSTEM_PROMPT + _current_datetime_line() + memory_prompt_block() + _funny_voice_prompt_line() + _timer_prompt_line() + _critical_reminders_prompt_line() + _uncertain_classification_prompt_line()
    messages = (history or []) + [
        {"role": "user", "content": f"[The user spoke in {language_name}] {user_text}"}
    ]

    try:
        response = _timed(
            "claude",
            client.messages.create,
            model=CLAUDE_MODEL, max_tokens=300, system=system_prompt, tools=lang_tools, messages=messages,
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
                    "content": _timed(
                        f"tool:{block.name}", execute_tool, block.name, language, block.input, out_device
                    ),
                }
                for block in response.content
                if block.type == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
            # Recompute in case a tool (like set_voice_mode or remember) updated state
            system_prompt = SYSTEM_PROMPT + _current_datetime_line() + memory_prompt_block() + _funny_voice_prompt_line() + _timer_prompt_line() + _critical_reminders_prompt_line() + _uncertain_classification_prompt_line()
            response = _timed(
                "claude",
                client.messages.create,
                model=CLAUDE_MODEL, max_tokens=300, system=system_prompt, tools=lang_tools, messages=messages,
            )

        if response.stop_reason == "tool_use":
            # Hit the round cap while Claude still wanted to call another
            # tool. That response's text (if any) is just in-progress
            # "here's what I'll try next" narration, not a real answer --
            # confirmed: text like "Let me search more directly for..." got
            # read aloud verbatim once. Discard it and force one final,
            # tool-free turn on the same history so Claude commits to its
            # best answer from what it's already gathered.
            system_prompt = SYSTEM_PROMPT + _current_datetime_line() + memory_prompt_block() + _funny_voice_prompt_line() + _timer_prompt_line() + _critical_reminders_prompt_line() + _uncertain_classification_prompt_line()
            response = _timed(
                "claude_forced_final",
                client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=300,
                system=system_prompt,
                tools=lang_tools,
                tool_choice={"type": "none"},
                messages=messages,
            )
    except Exception as exc:
        raise BrainError(f"Claude request failed: {exc}") from exc

    reply = "".join(block.text for block in response.content if block.type == "text").strip()
    if not reply:
        reply = _get_empty_reply_fallback(language, timeline, _last_tool_result_str(messages))

    # Defensive correction: Claude occasionally ignores the "always reply in
    # the user's language" system-prompt instruction anyway -- confirmed: an
    # unusual meta/identity question in Hebrew got an English reply despite
    # the whole conversation being Hebrew-locked. detect_language() is
    # reliable here (unlike on transcribed audio) since Claude's own
    # generated text is never transliterated gibberish -- see
    # brain/language.py's docstring. One bounded retry, on a throwaway copy
    # of the message list so the correction round-trip itself never pollutes
    # the real history returned to the caller; if a stronger one-line
    # instruction doesn't fix it, looping again isn't likely to either, so
    # this falls back to the original (wrong-language) reply rather than
    # failing the whole turn.
    if reply and detect_language(reply) != language:
        retry_messages = messages + [
            {"role": "assistant", "content": response.content},
            {
                "role": "user",
                "content": f"That reply must be in {language_name}, not the language you just used. Say the same thing again, in {language_name} only.",
            },
        ]
        try:
            retry_response = _timed(
                "claude_language_retry",
                client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=300,
                system=system_prompt,
                tools=lang_tools,
                tool_choice={"type": "none"},
                messages=retry_messages,
            )
            retried_reply = "".join(block.text for block in retry_response.content if block.type == "text").strip()
            if retried_reply:
                reply = retried_reply
        except Exception:
            pass

    # Force silent replies for stop/cancel/play tools as requested by user ("you don't need to say עצרתי" or "בוצע")
    tool_stages = [stage for stage, _ in timeline if stage.startswith("tool:")]
    if tool_stages:
        last_tool = tool_stages[-1].replace("tool:", "")
        if "stop" in last_tool or "cancel" in last_tool:
            reply = ""
        elif "play_music" in last_tool or "seek_music" in last_tool or "skip_track" in last_tool or "stop_music" in last_tool or "get_daily_halacha" in last_tool:
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

    # A bare "stop" must never get a spoken reply, even when there was
    # nothing to stop (no tool ended up being called) -- the user never
    # wants an acknowledgment like "Okay!" for this word, only the action.
    if any(w in user_text.lower() for w in STOP_WORDS):
        reply = ""

    reply = _strip_voice_unfriendly_formatting(reply)
    messages.append({"role": "assistant", "content": response.content})
    return reply, messages, timeline
