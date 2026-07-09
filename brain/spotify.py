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


def _get_client():
    """Lazily build an authenticated spotipy client, cached across calls."""
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
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError as exc:
        raise SpotifyError("spotipy not installed -- run `uv sync`") from exc

    try:
        _client = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=SPOTIPY_CLIENT_ID,
                client_secret=SPOTIPY_CLIENT_SECRET,
                redirect_uri=SPOTIPY_REDIRECT_URI,
                scope=_SCOPE,
                cache_path=str(_CACHE_PATH),
                # Don't try to pop a browser mid-voice-call; first-time auth is
                # done out of band via `python -m brain.spotify` (see module docstring).
                open_browser=False,
            )
        )
    except Exception as exc:
        raise SpotifyError(f"Spotify authorization failed: {exc}") from exc
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


def play(query: str) -> str:
    """Search for `query` and start playing the top matching track. Returns a
    short status string for Claude to relay; raises SpotifyError on failure."""
    query = (query or "").strip()
    if not query:
        return "No song was specified to play."

    sp = _get_client()
    try:
        results = sp.search(q=query, type="track", limit=1)
        items = results.get("tracks", {}).get("items", [])
        if not items:
            return f"Couldn't find anything on Spotify for '{query}'."

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
        raise SpotifyError(f"Couldn't stop Spotify: {exc}") from exc
    return "Stopped the music."


if __name__ == "__main__":
    # One-time setup / smoke test: authorizes (creating .spotify-cache) and
    # lists available Spotify devices. Run once interactively:
    #   uv run python -m brain.spotify
    _sp = _get_client()
    _devices = _sp.devices().get("devices", [])
    if not _devices:
        print("Authorized. No Spotify devices found right now -- open Spotify somewhere on this account.")
    else:
        print("Authorized. Available Spotify devices:")
        for _d in _devices:
            _active = " [active]" if _d.get("is_active") else ""
            print(f"  - {_d['name']} ({_d['type']}){_active}")
