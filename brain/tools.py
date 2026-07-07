"""Tool definitions for Claude's tool-calling.

Most tools here are still stubs -- those features aren't actually built yet
(no real timer, no Spotify integration, no telephony). The point of
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

from . import memory
from .language import LANGUAGE_NAMES
from .websearch import WebSearchError, search

TOOLS = [
    # Timer
    {
        "name": "set_timer",
        "description": "Set a countdown timer for a given duration.",
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
        "name": "cancel_timer",
        "description": "Cancel a running countdown timer.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # Music / volume
    {
        "name": "play_music",
        "description": "Play a song, artist, or genre of music.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to play, e.g. a song title, artist, or genre.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "stop_music",
        "description": "Stop any currently playing music.",
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
]

# Tools not listed here default to both languages.
TOOL_LANGUAGES: dict[str, list[str]] = {
    "get_daily_halacha": ["he"],
    "ask_mishna_question": ["he"],
    "tell_joke_english": ["en"],
    "tell_joke_hebrew": ["he"],
}


def execute_tool(name: str, language: str, tool_input: dict) -> str:
    """Dispatch a tool call. Every tool except `web_search` is still a stub --
    always returns "not built yet", except when `language` isn't one this
    skill will support (see TOOL_LANGUAGES), which returns a language-mismatch
    message instead.
    """
    allowed = TOOL_LANGUAGES.get(name, ["en", "he"])
    if language not in allowed:
        allowed_name = LANGUAGE_NAMES[allowed[0]]
        return (
            f"This skill is only available in {allowed_name}. Tell the user, in "
            f"{LANGUAGE_NAMES[language]}, that they should try asking in {allowed_name} instead."
        )

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

    return (
        f"The '{name}' feature isn't built yet. Tell the user plainly that this "
        "isn't available right now -- don't imply it happened."
    )
