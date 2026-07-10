"""Spotify playback for the play_music / stop_music tools.

Controls playback through the Spotify Web API (via spotipy). Important: the Web
API does NOT produce audio itself -- it drives an existing Spotify Connect
device (the Spotify app on a phone/computer, or a librespot/raspotify daemon on
the Pi). Controlling playback requires a Spotify Premium account and an active
device to play on.

Credentials come from SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET /
SPOTIPY_REDIRECT_URI in .env (see config.py). The first authorization is a
one-time OAuth flow -- run `uv run python -m brain.spotify` once interactively
to authorize; the token is cached in .spotify-cache (gitignored) and reused
afterward, including headlessly on the Pi.

spotipy itself is imported lazily inside _get_client so that importing this
module (which brain/tools.py does at startup) never requires the dependency or
valid credentials until music is actually requested -- same reason the other
tool stubs stay cheap to import.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI

# Minimum scopes to search, see available devices, and start/pause playback.
_SCOPE = "user-read-playback-state user-modify-playback-state"
# Cache the OAuth token next to the other gitignored local state so the
# one-time browser authorization isn't needed on every run.
_CACHE_PATH = Path(__file__).parent.parent / ".spotify-cache"

_client = None


class SpotifyError(Exception):
    pass


def _make_auth(open_browser: bool):
    """Build a SpotifyOAuth. `open_browser` controls whether it may launch the
    interactive first-time authorization flow (browser + loopback capture) --
    only ever True for the one-time `python -m brain.spotify` setup, never from
    inside the daemon."""
    from spotipy.oauth2 import SpotifyOAuth

    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=_SCOPE,
        cache_path=str(_CACHE_PATH),
        open_browser=open_browser,
    )


def _get_client():
    """Lazily build an authenticated spotipy client, cached across calls.

    Never triggers interactive auth: if there's no cached token yet, this
    raises SpotifyError telling the user to run the one-time setup, rather than
    blocking on stdin ("Enter the URL you were redirected to:") inside the
    voice daemon -- which can't be answered mid-call and hangs the request.
    """
    global _client
    if _client is not None:
        return _client

    if not (SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET and SPOTIPY_REDIRECT_URI):
        raise SpotifyError(
            "Spotify credentials not set -- add SPOTIPY_CLIENT_ID, "
            "SPOTIPY_CLIENT_SECRET, and SPOTIPY_REDIRECT_URI to .env"
        )
    try:
        import spotipy
    except ImportError as exc:
        raise SpotifyError("spotipy not installed -- run `uv sync`") from exc

    auth = _make_auth(open_browser=False)
    # A cached-but-expired token is fine -- spotipy refreshes it silently via
    # the refresh token on the next API call. Only a *missing* token would
    # trigger the interactive flow, so guard against exactly that.
    if auth.cache_handler.get_cached_token() is None:
        raise SpotifyError(
            "Spotify isn't authorized on this machine yet -- run "
            "`uv run python -m brain.spotify` once to log in, then try again"
        )
    _client = spotipy.Spotify(auth_manager=auth)
    return _client


def _active_device_id(sp) -> str | None:
    """Prefer the currently-active device; fall back to any available one."""
    devices = sp.devices().get("devices", [])
    if not devices:
        return None
    for device in devices:
        if device.get("is_active"):
            return device["id"]
    return devices[0]["id"]


def _clean_hebrew_query(query: str) -> str:
    """Strip common helper prefix/suffix words in Hebrew search queries."""
    words_to_remove = [
        "נגן לי את השיר",
        "נגן את השיר",
        "תשמיע את השיר",
        "תנגן את השיר",
        "תשמיע לי את",
        "נגן לי את",
        "נגן את",
        "תשמיע את",
        "תנגן את",
        "בספוטיפיי",
        "ספוטיפיי",
        "השיר",
        "שיר",
        "נגן",
        "נגני",
        "תשמיע",
        "תשמיעי",
        "תנגן",
        "תנגני",
        "את",
        "לי",
        "ישמור",
    ]
    query = " ".join(query.split())
    for phrase in words_to_remove:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        query = re.sub(pattern, "", query, flags=re.IGNORECASE)
    return " ".join(query.split())


def play(query: str) -> str:
    """Search for `query` and start playing the top matching track (or play a URI directly).
    Returns a short status string for Claude to relay; raises SpotifyError on failure."""
    query = (query or "").strip()
    if not query:
        return "No song was specified to play."

    sp = _get_client()
    try:
        if query.startswith("spotify:track:"):
            # Fetch track details directly from the URI
            track_id = query.split(":")[-1]
            track = sp.track(track_id)
        else:
            cleaned_query = _clean_hebrew_query(query)
            # If cleaning leaves nothing, fall back to the original query
            search_query = cleaned_query if cleaned_query else query
            results = sp.search(q=search_query, type="track", limit=5)
            items = results.get("tracks", {}).get("items", [])
            if not items:
                return f"Couldn't find anything on Spotify for '{search_query}'."
            track = items[0]

        device_id = _active_device_id(sp)
        if device_id is None:
            return (
                "Found the song, but there's no Spotify device to play on right "
                "now. Open Spotify on a phone or computer (or start the Pi's "
                "Spotify player) on the same account, then ask again."
            )
        sp.start_playback(device_id=device_id, uris=[track["uri"]])
    except SpotifyError:
        raise
    except Exception as exc:
        raise SpotifyError(f"Spotify playback failed: {exc}") from exc

    artists = ", ".join(a["name"] for a in track.get("artists", []))
    return f"Now playing '{track['name']}'" + (f" by {artists}." if artists else ".")


def stop() -> str:
    """Pause playback on the active device. Returns a short status string;
    raises SpotifyError on failure."""
    sp = _get_client()
    try:
        device_id = _active_device_id(sp)
        if device_id is None:
            return "There's no active Spotify device to stop."
        sp.pause_playback(device_id=device_id)
    except SpotifyError:
        raise
    except Exception as exc:
        err_msg = str(exc)
        if "Restriction violated" in err_msg or "already paused" in err_msg.lower():
            return "Stopped the music (already paused)."
        raise SpotifyError(f"Couldn't stop Spotify: {exc}") from exc
    return "Stopped the music."


def is_playing() -> bool:
    """Check if there is active playback on Spotify."""
    try:
        sp = _get_client()
        playback = sp.current_playback()
        return playback is not None and playback.get("is_playing", False)
    except Exception:
        return False


def resume() -> str:
    """Resume playback on the active device. Returns a status string;
    raises SpotifyError on failure."""
    sp = _get_client()
    try:
        device_id = _active_device_id(sp)
        if device_id is None:
            return "There's no active Spotify device to resume."
        sp.start_playback(device_id=device_id)
    except Exception as exc:
        raise SpotifyError(f"Couldn't resume Spotify: {exc}") from exc
    return "Resumed playback."



if __name__ == "__main__":
    # One-time setup / smoke test: runs the interactive OAuth (opens a browser
    # and captures the redirect on the loopback URI), writing .spotify-cache,
    # then lists available devices. Run once on a machine with a browser:
    #   uv run python -m brain.spotify
    # Unlike _get_client(), this path is allowed to open the browser -- it's the
    # deliberate authorization step, not a mid-call request.
    import spotipy

    _sp = spotipy.Spotify(auth_manager=_make_auth(open_browser=True))
    _devices = _sp.devices().get("devices", [])
    if not _devices:
        print("Authorized. No Spotify devices found right now -- open Spotify somewhere on this account.")
    else:
        print("Authorized. Available Spotify devices:")
        for _d in _devices:
            _active = " [active]" if _d.get("is_active") else ""
            print(f"  - {_d['name']} ({_d['type']}){_active}")
