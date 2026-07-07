"""Tool definitions for Claude's tool-calling.

All tools here are currently stubs -- none of these features are actually
built yet (no real timer, no Spotify integration, no telephony). The point
of registering them anyway, rather than just relying on the system prompt to
say "you can't do this," is that prompt instructions alone didn't work in
practice: Claude kept confidently claiming to set timers and play music that
never happened. Giving it a real tool to call means it can only claim success
by actually invoking one and getting a result back -- when that result says
"not built yet," it has no way to pretend otherwise. As each feature gets
built for real, replace that tool's stub return value with the real action.

TOOL_LANGUAGES records which language(s) each skill will support once real --
some Jewish-content skills (daily halacha, Mishna Q&A) are Hebrew-only by
design, not just unimplemented. Kept as a separate mapping rather than a field
on the tool dict itself, since Anthropic's tool schema doesn't have a place
for it and TOOLS is sent to the API as-is.
"""
from __future__ import annotations

from .language import LANGUAGE_NAMES

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
    {
        "name": "take_photo",
        "description": "Take a photo using the device's camera.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

# Tools not listed here default to both languages.
TOOL_LANGUAGES: dict[str, list[str]] = {
    "get_daily_halacha": ["he"],
    "ask_mishna_question": ["he"],
    "tell_joke_english": ["en"],
    "tell_joke_hebrew": ["he"],
}


def execute_tool(name: str, language: str) -> str:
    """Every tool is a stub right now -- always returns "not built yet",
    except when `language` isn't one this skill will support (see
    TOOL_LANGUAGES), which returns a language-mismatch message instead.
    """
    allowed = TOOL_LANGUAGES.get(name, ["en", "he"])
    if language not in allowed:
        allowed_name = LANGUAGE_NAMES[allowed[0]]
        return (
            f"This skill is only available in {allowed_name}. Tell the user, in "
            f"{LANGUAGE_NAMES[language]}, that they should try asking in {allowed_name} instead."
        )
    return (
        f"The '{name}' feature isn't built yet. Tell the user plainly that this "
        "isn't available right now -- don't imply it happened."
    )
