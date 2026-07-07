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
"""
from __future__ import annotations

TOOLS = [
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
        "name": "answer_phone",
        "description": "Answer an incoming phone call.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def execute_tool(name: str) -> str:
    """Every tool is a stub right now -- always returns "not built yet"."""
    return (
        f"The '{name}' feature isn't built yet. Tell the user plainly that this "
        "isn't available right now -- don't imply it happened."
    )
