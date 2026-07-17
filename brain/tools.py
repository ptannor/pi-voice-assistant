"""Tool definitions for Claude's tool-calling.

Most tools here are still stubs -- those features aren't actually built yet
(no real timer, no telephony). The point of
registering them anyway, rather than just relying on the system prompt to say
"you can't do this," is that prompt instructions alone didn't work in
practice: Claude kept confidently claiming to set timers and play music that
never happened. Giving it a real tool to call means it can only claim success
by actually invoking one and getting a result back -- when that result says
"not built yet," it has no way to pretend otherwise. As each feature gets
built for real, replace that tool's stub return value with the real action.
`web_search` is the first real one -- see websearch.py.

TOOL_LANGUAGES records which language(s) each skill will support once real --
some Jewish-content skills (daily halacha, Mishna Q&A) are Hebrew-only by
design, not just unimplemented. Kept as a separate mapping rather than a field
on the tool dict itself, since Anthropic's tool schema doesn't have a place
for it and TOOLS is sent to the API as-is.
"""
from __future__ import annotations

from . import gcal, halacha, memory, spotify, timer
from .calculator import calculate
from .language import LANGUAGE_NAMES
from .mode import set_funny_voice
from .websearch import WebSearchError, search

TOOLS = [
    # Timer
    {
        "name": "set_timer_hebrew",
        "description": "Set a countdown timer for a given duration in seconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "integer",
                    "description": "How long the timer should run, in seconds.",
                }
            },
            "required": ["duration_seconds"],
        },
    },
    {
        "name": "cancel_timer_hebrew",
        "description": "Cancel a running countdown timer or stop any playing alarm music.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_timer_english",
        "description": "Set a countdown timer for a given duration in seconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "integer",
                    "description": "How long the timer should run, in seconds.",
                }
            },
            "required": ["duration_seconds"],
        },
    },
    {
        "name": "cancel_timer_english",
        "description": "Cancel a running countdown timer.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # Music / volume
    {
        "name": "play_music_hebrew",
        "description": "Play a song, artist, or genre of music on Spotify. To resume or continue paused music, pass 'resume' as the query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The song query, direct URI, or 'resume' to continue playback.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_music_hebrew",
        "description": "Search Spotify for a song and return a list of top candidate tracks with names, artists, popularity (0-100), and track URIs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The song query to search.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "stop_music_hebrew",
        "description": "Stop any currently playing music on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "play_music_english",
        "description": "Play a song, artist, or genre of music on Spotify. To resume or continue paused music, pass 'resume' as the query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The song query, direct URI, or 'resume' to continue playback.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_music_english",
        "description": "Search Spotify for a song and return a list of top candidate tracks with names, artists, popularity (0-100), and track URIs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The song query to search.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "stop_music_english",
        "description": "Stop any currently playing music on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "volume_up",
        "description": "Increase the speaker volume by one step.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "volume_down",
        "description": "Decrease the speaker volume by one step.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_volume",
        "description": "Set the speaker volume to a specific level.",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                    "description": "Volume level from 0 (mute) to 10 (maximum).",
                }
            },
            "required": ["level"],
        },
    },
    # Jewish household content (roadmap)
    {
        "name": "get_parsha",
        "description": "Get this week's Torah portion (Parashat HaShavua).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_zmanim",
        "description": (
            "Get halachic times (zmanim) for the user's location, e.g. candle-lighting, "
            "sunset, sunrise, or havdalah."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Which zman is being asked about, e.g. 'candle lighting time' or 'when is sunset'.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_daily_halacha",
        "description": "Get today's daily halacha (Jewish law) teaching.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask_mishna_question",
        "description": "Answer a question about a specific Mishna or Mishnaic topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The user's question about the Mishna."}
            },
            "required": ["question"],
        },
    },
    {
        "name": "tell_joke_english",
        "description": "Tell a family-friendly joke in English.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "tell_joke_hebrew",
        "description": "Tell a family-friendly joke in Hebrew.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # Misc (roadmap)
    {
        "name": "answer_phone",
        "description": "Answer an incoming phone call.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # Real (not a stub) -- see calculator.py
    {
        "name": "calculate",
        "description": (
            "Compute the exact result of an arithmetic expression (+ - * / // % **, "
            "parentheses). Always use this for any nontrivial arithmetic instead of "
            "computing it mentally -- e.g. multi-digit multiplication, anything with "
            "several steps, or an exponent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A Python-syntax arithmetic expression, e.g. '(53*72-23-15)**2'.",
                }
            },
            "required": ["expression"],
        },
    },
    # Real (not a stub) -- see mode.py/respond.py/llm.py's funny-voice hooks.
    # A kids' easter egg: a higher-pitched/child-like voice plus a fixed silly
    # Hebrew sign-off after every reply, toggled on/off by voice command.
    {
        "name": "set_voice_mode",
        "description": (
            "Switch the assistant's voice between normal and a silly 'funny voice' "
            "easter egg mode. Call this when the user explicitly asks to switch to "
            "funny/silly voice mode, or back to regular/normal voice mode."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["funny", "regular"],
                    "description": "'funny' for the silly easter egg voice, 'regular' for the normal voice.",
                }
            },
            "required": ["mode"],
        },
    },
    # Real (not a stub) -- see websearch.py
    {
        "name": "web_search",
        "description": (
            "Search the web for current information not in your training data, e.g. "
            "movie showtimes, current events, or anything else that needs an up-to-date answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query."}},
            "required": ["query"],
        },
    },
    # Real (not a stub) -- see memory.py. Already-remembered facts are listed
    # directly in the system prompt, so there's no separate "list memories"
    # tool -- only mutation needs one.
    {
        "name": "remember",
        "description": (
            "Save a fact or preference about this household to remember in future "
            "conversations, not just this one -- e.g. names, allergies, recurring "
            "preferences, house rules. Use it when the user shares something worth "
            "persisting long-term, or explicitly asks you to remember something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "The fact to remember, written plainly."}},
            "required": ["fact"],
        },
    },
    {
        "name": "forget",
        "description": "Remove a previously remembered fact, when the user asks you to forget something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text identifying which remembered fact to remove."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_household_info",
        "description": (
            "Search a household reference library (recipes, family member details, "
            "birthdays, school/activity schedules, and anything else added to it) for "
            "something specific. Use this when asked about that kind of detail, rather "
            "than relying on the smaller always-known facts alone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for, e.g. a name or topic."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "seek_music_hebrew",
        "description": "Seek forward or backward in the currently playing song on Spotify by a number of seconds. Positive values skip forward, negative values skip backward.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "The number of seconds to seek. Positive to go forward, negative to go backward (e.g. 30, -60).",
                }
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "seek_music_english",
        "description": "Seek forward or backward in the currently playing song on Spotify by a number of seconds. Positive values skip forward, negative values skip backward.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "The number of seconds to seek. Positive to go forward, negative to go backward (e.g. 30, -60).",
                }
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "skip_track_hebrew",
        "description": "Skip the current song and play the next song, or go back to play the previous song on Spotify.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["next", "previous"],
                    "description": "The direction to skip. Use 'next' to go to the next song, and 'previous' to go to the previous song. Default is 'next'.",
                }
            },
            "required": ["direction"],
        },
    },
    {
        "name": "skip_track_english",
        "description": "Skip the current song and play the next song, or go back to play the previous song on Spotify.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["next", "previous"],
                    "description": "The direction to skip. Use 'next' to go to the next song, and 'previous' to go to the previous song. Default is 'next'.",
                }
            },
            "required": ["direction"],
        },
    },
    # Real (not a stub) -- see gcal.py. Language-neutral (not split into
    # _hebrew/_english variants like music/timers): the inputs here are
    # structured fields Claude has already normalized out of freeform speech,
    # not raw query text, so one tool per action is enough.
    {
        "name": "add_calendar_event",
        "description": (
            "Add a reminder to Mendy's calendar -- e.g. a medication schedule, "
            "appointment, or other recurring obligation. For something like "
            "'antibiotics at 8am and 8pm every day for 10 days', pass both times "
            "in the same call so they're linked as one reminder."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title, e.g. 'Antibiotics'."},
                "date": {
                    "type": "string",
                    "description": "The first/only date this should start, as YYYY-MM-DD, resolved from the current date.",
                },
                "times": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One or more times of day in 24h HH:MM, e.g. ['08:00', '20:00'].",
                },
                "recurrence": {
                    "type": "string",
                    "enum": ["none", "daily", "weekly"],
                    "description": "How often it repeats. 'none' for a one-time reminder.",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of occurrences per time-of-day, e.g. 10 for 'for 10 days'. Omit if using until_date, or for a one-time reminder.",
                },
                "until_date": {
                    "type": "string",
                    "description": "Last date it repeats, as YYYY-MM-DD, e.g. for 'until next Friday'. Omit if using count.",
                },
                "notes": {"type": "string", "description": "Any extra detail worth keeping, otherwise omit."},
            },
            "required": ["title", "date", "times", "recurrence"],
        },
    },
    {
        "name": "list_calendar_events",
        "description": "List upcoming reminders on Mendy's calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to look. Default 60 -- pass a smaller value for a narrower question (e.g. 'what do I have today/this week'), or a larger one for questions further out.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "cancel_calendar_event",
        "description": "Cancel/remove a reminder from Mendy's calendar, matched by a description of its title.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text identifying which reminder to cancel, e.g. 'antibiotics'.",
                }
            },
            "required": ["query"],
        },
    },
]

# Tools not listed here default to both languages.
TOOL_LANGUAGES: dict[str, list[str]] = {
    "get_daily_halacha": ["he"],
    "ask_mishna_question": ["he"],
    "tell_joke_english": ["en"],
    "tell_joke_hebrew": ["he"],
    "seek_music_hebrew": ["he"],
    "seek_music_english": ["en"],
    "skip_track_hebrew": ["he"],
    "skip_track_english": ["en"],
    # Music playback is split by language: Hebrew tools are Hebrew-only,
    # English tools are English-only.
    "play_music_hebrew": ["he"],
    "search_music_hebrew": ["he"],
    "stop_music_hebrew": ["he"],
    "play_music_english": ["en"],
    "search_music_english": ["en"],
    "stop_music_english": ["en"],
    # Timers are also split by language.
    "set_timer_hebrew": ["he"],
    "cancel_timer_hebrew": ["he"],
    "set_timer_english": ["en"],
    "cancel_timer_english": ["en"],
}


def get_tools_for_language(language: str) -> list[dict]:
    """Filter the global TOOLS list, returning only tools allowed for `language`."""
    return [
        tool for tool in TOOLS
        if language in TOOL_LANGUAGES.get(tool["name"], ["en", "he"])
    ]


def execute_tool(name: str, language: str, tool_input: dict, out_device=None) -> str:
    """Dispatch a tool call. Every tool except `web_search` is still a stub --
    always returns "not built yet", except when `language` isn't one this
    skill will support (see TOOL_LANGUAGES), which returns a language-mismatch
    message instead.

    `out_device` (an audio_check.devices.Device, or None) is only used by
    set_timer_hebrew/english, which need somewhere to loop the timer sound
    when it finishes -- see brain/timer.py.
    """
    allowed = TOOL_LANGUAGES.get(name, ["en", "he"])
    if language not in allowed:
        allowed_name = LANGUAGE_NAMES[allowed[0]]
        return (
            f"This skill is only available in {allowed_name}. Tell the user, in "
            f"{LANGUAGE_NAMES[language]}, that they should try asking in {allowed_name} instead."
        )

    if name == "calculate":
        return calculate(tool_input["expression"])

    if name == "set_voice_mode":
        funny = tool_input["mode"] == "funny"
        set_funny_voice(funny)
        # Deliberately not a natural-language English sentence -- confirmed
        # that biased Claude into echoing/paraphrasing this tool result in
        # English even mid-Hebrew-conversation, overriding the "reply in the
        # user's language" instruction. A neutral status has nothing to echo.
        return "ok"

    if name == "web_search":
        try:
            return search(tool_input["query"])
        except WebSearchError as exc:
            return f"Web search failed ({exc}). Tell the user you couldn't search right now."

    if name == "remember":
        return memory.remember(tool_input["fact"])

    if name == "forget":
        return memory.forget(tool_input["query"])

    if name == "search_household_info":
        return memory.search_household_info(tool_input["query"])

    if name in ("play_music_hebrew", "play_music_english"):
        try:
            return spotify.play(tool_input["query"])
        except spotify.SpotifyError as exc:
            return f"status: error_playback_failed, details: {exc}"

    if name in ("search_music_hebrew", "search_music_english"):
        try:
            return spotify.search_track(tool_input["query"])
        except spotify.SpotifyError as exc:
            return f"status: error_search_failed, details: {exc}"

    if name in ("seek_music_hebrew", "seek_music_english"):
        try:
            return spotify.seek(tool_input["seconds"])
        except spotify.SpotifyError as exc:
            return f"status: error_seek_failed, details: {exc}"

    if name in ("skip_track_hebrew", "skip_track_english"):
        try:
            return spotify.skip_track(tool_input.get("direction", "next"))
        except spotify.SpotifyError as exc:
            return f"status: error_skip_failed, details: {exc}"

    if name in ("stop_music_hebrew", "stop_music_english"):
        try:
            return spotify.stop()
        except spotify.SpotifyError as exc:
            return f"status: error_stop_failed, details: {exc}"

    if name in ("set_timer_hebrew", "set_timer_english"):
        return timer.set_timer(tool_input["duration_seconds"], out_device)

    if name in ("cancel_timer_hebrew", "cancel_timer_english"):
        return timer.cancel_timer()

    if name == "add_calendar_event":
        try:
            return gcal.add_event(
                title=tool_input["title"],
                date=tool_input["date"],
                times=tool_input["times"],
                recurrence=tool_input.get("recurrence", "none"),
                count=tool_input.get("count"),
                until_date=tool_input.get("until_date"),
                notes=tool_input.get("notes", ""),
            )
        except gcal.CalendarError as exc:
            return f"status: error_calendar_failed, details: {exc}"

    if name == "list_calendar_events":
        try:
            return gcal.list_events(tool_input.get("days_ahead", 60))
        except gcal.CalendarError as exc:
            return f"status: error_calendar_failed, details: {exc}"

    if name == "cancel_calendar_event":
        try:
            return gcal.cancel_events(query=tool_input["query"])
        except gcal.CalendarError as exc:
            return f"status: error_calendar_failed, details: {exc}"

    if name == "get_daily_halacha":
        episode = halacha.pick_short_halacha_episode()
        if episode:
            try:
                spotify.play(episode["uri"])
                return f"status: playing, track: {episode['name']}, artist: הלכה יומית"
            except spotify.SpotifyError:
                pass  # fall through to the TTS-composed teaching below
        return halacha.get_daily_halacha_text(language)

    if name == "get_zmanim":
        from datetime import datetime

        from shabbat.config import load_config
        from shabbat.hebcal_client import get_data
        from shabbat.schedule import build_windows, concise_times_text

        config = load_config()
        items, status = get_data(config)
        if items is None:
            return "status: error_zmanim_unavailable"
        windows = build_windows(items)
        now = datetime.now().astimezone()
        return concise_times_text(windows, now, language)

    if name == "tell_joke_hebrew":
        import random
        queries = [
            "בדיחה קצרה מצחיקה לילדים",
            "בדיחות קצרות ומצחיקות",
            "בדיחה מצחיקה רצח",
            "בדיחה קורעת מצחיקה",
            "בדיחות קרש מצחיקות לילדים"
        ]
        q = random.choice(queries)
        try:
            return search(q)
        except Exception:
            return "No Google connection is active or search failed. Please tell one of your own best family-friendly jokes in Hebrew from your world knowledge!"

    if name == "tell_joke_english":
        import random
        queries = [
            "funny short dad joke family friendly",
            "clean hilarious short joke",
            "funny one liner jokes kids",
            "silly family friendly joke of the day"
        ]
        q = random.choice(queries)
        try:
            return search(q)
        except Exception:
            return "No Google connection is active or search failed. Please tell one of your own best family-friendly jokes in English from your world knowledge!"

    return (
        f"The '{name}' feature isn't built yet. Tell the user plainly that this "
        "isn't available right now -- don't imply it happened."
    )
