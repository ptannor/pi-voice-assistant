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

import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import (
    SPOTIFY_DEVICE_NAME,
    SPOTIPY_CLIENT_ID,
    SPOTIPY_CLIENT_SECRET,
    SPOTIPY_REDIRECT_URI,
)

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

    # spotipy logs every non-2xx response as an ERROR before raising (e.g.
    # "HTTP Error for PUT .../pause ... returned 403 due to Player command
    # failed: Restriction violated") -- noisy on literally every conversation
    # turn that doesn't involve music, since stop()/pause_playback() is called
    # unconditionally on every wake word (see audio_focus.py's _pause_spotify,
    # which deliberately doesn't gate this on is_playing() -- that check lags
    # behind reality right when it matters most, see its own comment). Our
    # code already classifies this exact case as a non-error (stop() maps it
    # to "status: stopped"), so spotipy's own duplicate print is pure noise
    # that was burying real errors in the console; only its own internal
    # pre-emptive log is silenced here, not our error handling/messages.
    import logging
    logging.getLogger("spotipy.client").setLevel(logging.CRITICAL)

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


_SPOTIFY_URI_RE = re.compile(r"^spotify:(track|episode|show|playlist|album):[A-Za-z0-9]+$")


def _run_applescript(script: str) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def _local_spotify_running() -> bool:
    if sys.platform != "darwin":
        return False
    res = _run_applescript('application "Spotify" is running')
    return res == "true"


def _local_device_name_hint() -> str:
    """The local machine's own short hostname (domain suffix stripped,
    lowercased) -- used to find "this machine's own Spotify Connect device"
    in _active_device_id below. Matched as a case-insensitive substring
    rather than an exact match, since the Spotify device name a Connect
    client registers isn't always identical to the OS hostname (raspotify's
    own device_name setting on the Pi, or macOS's system Spotify Connect
    naming), just related to it -- e.g. hostname "philpta-mbp.lan" against
    a registered device named "philpta-mbp".
    """
    return socket.gethostname().split(".")[0].lower()


def _active_device_id(sp) -> str | None:
    """Prefer whichever Spotify Connect device corresponds to the machine
    this script is actually running on (see _local_device_name_hint) --
    the household has many devices on one Spotify account (phones, other
    computers), and playback should always target the one the assistant
    itself runs from, not whichever device Spotify happens to consider
    "active" (that can be stale -- e.g. a phone last used hours ago still
    reporting is_active) or an arbitrary pick.

    Falls back to SPOTIFY_DEVICE_NAME (a manual override for when the local
    hostname doesn't usefully match this machine's registered device name
    at all), then Spotify's own is_active flag, then an arbitrary device.

    That last fallback used to be the default whenever nothing local
    matched -- confirmed broken: Spotify's device list order isn't "most
    recent" or "nearest", so this silently played on whichever device
    happened to sort first (once, a household member's phone instead of
    the intended speaker).
    """
    devices = sp.devices().get("devices", [])
    if not devices:
        return None

    host_hint = _local_device_name_hint()
    if host_hint:
        for device in devices:
            name = device.get("name", "").lower()
            if name and (host_hint in name or name in host_hint):
                return device["id"]

    if SPOTIFY_DEVICE_NAME:
        for device in devices:
            if SPOTIFY_DEVICE_NAME.lower() in device.get("name", "").lower():
                return device["id"]

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
        "play the song",
        "play song",
        "play the podcast",
        "play podcast",
        "play the episode",
        "play episode",
        "on spotify",
        "spotify",
        "בספוטיפיי",
        "ספוטיפיי",
        "הפודקאסטים",
        "פודקאסטים",
        "הפודקאסט",
        "פודקאסט",
        "הפודקסט",
        "פודקסט",
        "podcast",
        "הפרקים",
        "פרקים",
        "הפרק",
        "פרק",
        "episode",
        "התוכנית",
        "תוכנית",
        "התכנית",
        "תכנית",
        "השיר",
        "שיר",
        "song",
        "נגן",
        "נגני",
        "תשמיע",
        "תשמיעי",
        "תנגן",
        "תנגני",
        "play",
        "את",
        "לי",
        "ישמור",
    ]
    query = " ".join(query.split())
    for phrase in words_to_remove:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        query = re.sub(pattern, "", query, flags=re.IGNORECASE)
    return " ".join(query.split())


def _get_recommendations(sp, track) -> list[str]:
    """Generates a list of track URIs of the same style/artist to play after the target track."""
    import random
    uris = []
    try:
        artists = track.get("artists", [])
        if not artists:
            return uris
        
        primary_artist = artists[0]["name"]
        
        # 1. Fetch top tracks by the same artist (up to 8 tracks)
        results = sp.search(q=f'artist:"{primary_artist}"', type="track", limit=10)
        items = results.get("tracks", {}).get("items", [])
        for item in items:
            uri = item.get("uri")
            if uri and uri != track["uri"] and uri not in uris:
                uris.append(uri)
                
        # 2. Add some variety by mixing in top tracks from similar/compatible artists
        is_hebrew = any('\u0590' <= c <= '\u05fe' for c in primary_artist)
        if is_hebrew:
            # Popular Hebrew artists list
            hebrew_artists = ["חנן בן ארי", "ישי ריבו", "עומר אדם", "עדן חסון", "אושר כהן", "טונה", "רביד פלוטניק", "בניה ברבי"]
            compat = [a for a in hebrew_artists if a.lower() != primary_artist.lower()]
            if compat:
                selected_artists = random.sample(compat, min(2, len(compat)))
                for artist in selected_artists:
                    res = sp.search(q=f'artist:"{artist}"', type="track", limit=3)
                    for item in res.get("tracks", {}).get("items", []):
                        uri = item.get("uri")
                        if uri and uri != track["uri"] and uri not in uris:
                            uris.append(uri)
        else:
            # Popular English artists list
            english_artists = ["Billy Joel", "Elton John", "Coldplay", "Ed Sheeran", "Adele", "OneRepublic", "Queen"]
            compat = [a for a in english_artists if a.lower() != primary_artist.lower()]
            if compat:
                selected_artists = random.sample(compat, min(2, len(compat)))
                for artist in selected_artists:
                    res = sp.search(q=f'artist:"{artist}"', type="track", limit=3)
                    for item in res.get("tracks", {}).get("items", []):
                        uri = item.get("uri")
                        if uri and uri != track["uri"] and uri not in uris:
                            uris.append(uri)
    except Exception as exc:
        print(f"Failed to generate recommendations: {exc}", file=sys.stderr)
        
    return uris[:15]


def play(query: str) -> str:
    """Search for `query` and start playing the top matching track (or play a URI directly).
    Returns a short status string for Claude to relay; raises SpotifyError on failure."""
    is_resume = not query or query.strip().lower() in ("resume", "continue", "תמשיך", "להמשיך", "play", "פליי", "נגן")
    sp = _get_client()
    try:
        if is_resume:
            device_id = _active_device_id(sp)
            if device_id is None:
                if _local_spotify_running():
                    _run_applescript('tell application "Spotify" to play')
                    return "status: resumed"
                return "status: error_no_active_device"
            try:
                sp.start_playback(device_id=device_id)
            except Exception:
                if _local_spotify_running():
                    _run_applescript('tell application "Spotify" to play')
                    return "status: resumed"
                raise
            return "status: resumed"
    except Exception as exc:
        raise SpotifyError(f"Spotify resume failed: {exc}") from exc

    try:
        # Check if the query is a URI
        if query.startswith("spotify:"):
            uri = query
            if uri.startswith("spotify:track:"):
                track_id = uri.split(":")[-1]
                track = sp.track(track_id)
                name = track["name"]
                artists = ", ".join(a["name"] for a in track.get("artists", []))
            elif uri.startswith("spotify:episode:"):
                episode_id = uri.split(":")[-1]
                episode = sp.episode(episode_id)
                name = episode["name"]
                artists = episode.get("show", {}).get("name", "Podcast")
            elif uri.startswith("spotify:show:"):
                show_id = uri.split(":")[-1]
                show = sp.show(show_id)
                name = show["name"]
                artists = show.get("publisher", "Podcast")
            else:
                name = "Spotify item"
                artists = ""
        else:
            # Clean and search
            cleaned_query = _clean_hebrew_query(query)
            search_query = cleaned_query if cleaned_query else query
            
            is_podcast_intent = any(w in query.lower() for w in ("פודקאסט", "פודקסט", "podcast", "פרק", "episode", "תוכנית", "תכנית"))
            if is_podcast_intent:
                # Search for episodes and shows
                results = sp.search(q=search_query, type="episode,show", limit=5)
                episodes = results.get("episodes", {}).get("items", [])
                shows = results.get("shows", {}).get("items", [])
                if episodes:
                    item = episodes[0]
                    uri = item["uri"]
                    name = item["name"]
                    artists = item.get("show", {}).get("name", "Podcast")
                elif shows:
                    item = shows[0]
                    uri = item["uri"]
                    name = item["name"]
                    artists = item.get("publisher", "Podcast")
                else:
                    return f"status: error_not_found, query: {search_query}"
            else:
                # Default track search
                results = sp.search(q=search_query, type="track", limit=5)
                items = results.get("tracks", {}).get("items", [])
                if not items:
                    return f"status: error_not_found, query: {search_query}"
                track = items[0]
                uri = track["uri"]
                name = track["name"]
                artists = ", ".join(a["name"] for a in track.get("artists", []))

        device_id = _active_device_id(sp)
        if device_id is None:
            if _local_spotify_running():
                if not _SPOTIFY_URI_RE.match(uri):
                    raise SpotifyError(f"Refusing to pass malformed URI to AppleScript: {uri!r}")
                _run_applescript(f'tell application "Spotify" to play track "{uri}"')
                return f"status: playing, track: {name}, artist: {artists}"
            return "status: error_no_active_device"
        
        if uri.startswith("spotify:track:"):
            rec_uris = _get_recommendations(sp, track)
            sp.start_playback(device_id=device_id, uris=[uri] + rec_uris)
        elif uri.startswith("spotify:episode:"):
            sp.start_playback(device_id=device_id, uris=[uri])
        elif uri.startswith("spotify:show:") or uri.startswith("spotify:playlist:") or uri.startswith("spotify:album:"):
            sp.start_playback(device_id=device_id, context_uri=uri)
        else:
            sp.start_playback(device_id=device_id, uris=[uri])
            
    except SpotifyError:
        raise
    except Exception as exc:
        raise SpotifyError(f"Spotify playback failed: {exc}") from exc

    return f"status: playing, track: {name}, artist: {artists}"


def seek(seconds: int) -> str:
    """Seek forward (positive seconds) or backward (negative seconds) in the current track.
    Returns a status string; raises SpotifyError on failure."""
    sp = _get_client()
    try:
        device_id = _active_device_id(sp)
        if device_id is None:
            if _local_spotify_running():
                curr_pos_str = _run_applescript('tell application "Spotify" to get player position')
                try:
                    curr_pos = float(curr_pos_str)
                except ValueError:
                    curr_pos = 0.0
                new_pos = max(0.0, curr_pos + seconds)
                _run_applescript(f'tell application "Spotify" to set player position to {new_pos}')
                return "status: seeked"
            return "status: error_no_active_device"

        playback = sp.current_playback()
        if not playback or not playback.get("item"):
            return "status: error_not_playing"

        curr_progress_ms = playback.get("progress_ms", 0)
        new_progress_ms = max(0, curr_progress_ms + (seconds * 1000))
        sp.seek_track(position_ms=new_progress_ms, device_id=device_id)
        return "status: seeked"
    except SpotifyError:
        raise
    except Exception as exc:
        raise SpotifyError(f"Spotify seek failed: {exc}") from exc


def skip_track(direction: str = "next") -> str:
    """Skip to the next or previous track. direction can be 'next' or 'previous'.
    Returns a status string; raises SpotifyError on failure."""
    sp = _get_client()
    try:
        device_id = _active_device_id(sp)
        if device_id is None:
            if _local_spotify_running():
                cmd = "next track" if direction == "next" else "previous track"
                _run_applescript(f'tell application "Spotify" to {cmd}')
                return "status: skipped"
            return "status: error_no_active_device"

        if direction == "next":
            sp.next_track(device_id=device_id)
        else:
            sp.previous_track(device_id=device_id)
        return "status: skipped"
    except Exception as exc:
        if _local_spotify_running():
            cmd = "next track" if direction == "next" else "previous track"
            _run_applescript(f'tell application "Spotify" to {cmd}')
            return "status: skipped"
        raise SpotifyError(f"Spotify skip track failed: {exc}") from exc


def stop() -> str:
    """Pause playback on the active device. Returns a short status string;
    raises SpotifyError on failure."""
    sp = _get_client()
    try:
        device_id = _active_device_id(sp)
        if device_id is None:
            if _local_spotify_running():
                _run_applescript('tell application "Spotify" to pause')
                return "status: stopped"
            return "status: error_no_active_device"
        sp.pause_playback(device_id=device_id)
    except Exception as exc:
        if _local_spotify_running():
            _run_applescript('tell application "Spotify" to pause')
            return "status: stopped"
        err_msg = str(exc)
        if "Restriction violated" in err_msg or "already paused" in err_msg.lower():
            return "status: stopped"
        raise SpotifyError(f"Couldn't stop Spotify: {exc}") from exc
    return "status: stopped"


def is_playing() -> bool:
    """Check if there is active playback on Spotify."""
    try:
        sp = _get_client()
        playback = sp.current_playback()
        if playback is not None:
            return playback.get("is_playing", False)
        if _local_spotify_running():
            state = _run_applescript('tell application "Spotify" to player state')
            return state == "playing"
        return False
    except Exception:
        return False


def resume() -> str:
    """Resume playback on the active device. Returns a status string;
    raises SpotifyError on failure."""
    sp = _get_client()
    try:
        device_id = _active_device_id(sp)
        if device_id is None:
            if _local_spotify_running():
                _run_applescript('tell application "Spotify" to play')
                return "Resumed playback."
            return "There's no active Spotify device to resume."
        sp.start_playback(device_id=device_id)
    except Exception as exc:
        if _local_spotify_running():
            _run_applescript('tell application "Spotify" to play')
            return "Resumed playback."
        raise SpotifyError(f"Couldn't resume Spotify: {exc}") from exc
    return "Resumed playback."


# Fade-in on resume (see resume_playback_state). Ramps volume from a low floor
# up to the level captured when playback was backgrounded, over ~1.5s, so music
# eases back in after an interruption instead of snapping to full volume.
_FADE_START_PERCENT = 20
_FADE_DURATION_S = 1.5
_FADE_STEPS = 6


def capture_playback_state() -> dict | None:
    """Snapshot what's playing now -- track URI, position, context, and device
    volume -- so it can be restarted exactly where it left off later.

    Spotify's own pause/resume memory is lost the moment a *different* track is
    started on the same Connect device (e.g. a timer alarm plays over the music,
    see brain/timer.py). Capturing the position explicitly is what lets the
    original song resume after the alarm is dismissed. Returns None if nothing
    is playing / state can't be read."""
    try:
        sp = _get_client()
    except SpotifyError:
        sp = None
    if sp is not None:
        try:
            pb = sp.current_playback()
            if pb and pb.get("item"):
                item = pb["item"]
                device = pb.get("device") or {}
                ctx = pb.get("context") or {}
                return {
                    "source": "web",
                    "uri": item.get("uri"),
                    "position_ms": pb.get("progress_ms", 0) or 0,
                    "context_uri": ctx.get("uri"),
                    "volume": device.get("volume_percent"),
                    "device_id": device.get("id"),
                }
        except Exception:
            pass
    if _local_spotify_running():
        uri = _run_applescript('tell application "Spotify" to get id of current track')
        pos = _run_applescript('tell application "Spotify" to get player position')
        vol = _run_applescript('tell application "Spotify" to get sound volume')
        try:
            position_ms = int(float(pos) * 1000)
        except (TypeError, ValueError):
            position_ms = 0
        try:
            volume = int(vol)
        except (TypeError, ValueError):
            volume = None
        return {
            "source": "applescript",
            "uri": uri,
            "position_ms": position_ms,
            "context_uri": None,
            "volume": volume,
            "device_id": None,
        }
    return None


def _fade_volume_web(sp, device_id: str, target: int, abort_check) -> None:
    start = min(_FADE_START_PERCENT, target)
    step_dt = _FADE_DURATION_S / _FADE_STEPS
    try:
        for i in range(1, _FADE_STEPS + 1):
            if abort_check is not None and abort_check():
                break
            vol = int(start + (target - start) * i / _FADE_STEPS)
            sp.volume(vol, device_id=device_id)
            time.sleep(step_dt)
    except Exception:
        pass
    finally:
        # Always land on the intended level: a mid-ramp failure (e.g. a 429
        # volume rate-limit) or an abort must never strand the music quiet.
        try:
            sp.volume(target, device_id=device_id)
        except Exception:
            pass


def _fade_volume_applescript(target: int, abort_check) -> None:
    start = min(_FADE_START_PERCENT, target)
    step_dt = _FADE_DURATION_S / _FADE_STEPS
    try:
        for i in range(1, _FADE_STEPS + 1):
            if abort_check is not None and abort_check():
                break
            vol = int(start + (target - start) * i / _FADE_STEPS)
            _run_applescript(f'tell application "Spotify" to set sound volume to {vol}')
            time.sleep(step_dt)
    finally:
        _run_applescript(f'tell application "Spotify" to set sound volume to {target}')


def resume_playback_state(state: dict | None, fade: bool = True, abort_check=None) -> str:
    """Resume the playback captured by capture_playback_state(), restarting the
    exact track at its saved position and (if fade) ramping volume back up.

    `abort_check` is polled between fade steps; if it returns True (e.g. a
    higher-priority alarm has just grabbed the device) the ramp stops early but
    still restores the target volume. Best-effort: falls back to a bare resume()
    if the snapshot is missing or the explicit restart fails, so a resume never
    silently leaves the music stopped."""
    if not state or not state.get("uri"):
        return resume()

    target_vol = state.get("volume")
    if not isinstance(target_vol, int) or target_vol <= 0:
        target_vol = None
        fade = False

    if state.get("source") == "web":
        try:
            sp = _get_client()
        except SpotifyError:
            return resume()
        device_id = state.get("device_id") or _active_device_id(sp)
        if fade and device_id and target_vol:
            try:
                sp.volume(min(_FADE_START_PERCENT, target_vol), device_id=device_id)
            except Exception:
                fade = False
        try:
            if state.get("context_uri"):
                sp.start_playback(
                    device_id=device_id,
                    context_uri=state["context_uri"],
                    offset={"uri": state["uri"]},
                    position_ms=state["position_ms"],
                )
            else:
                sp.start_playback(
                    device_id=device_id,
                    uris=[state["uri"]],
                    position_ms=state["position_ms"],
                )
        except Exception:
            if target_vol and device_id:
                try:
                    sp.volume(target_vol, device_id=device_id)
                except Exception:
                    pass
            return resume()
        if fade and device_id and target_vol:
            _fade_volume_web(sp, device_id, target_vol, abort_check)
        return "status: resumed"

    # AppleScript (local macOS Spotify) fallback
    uri = state.get("uri") or ""
    if not _SPOTIFY_URI_RE.match(uri):
        _run_applescript('tell application "Spotify" to play')
        return "status: resumed"
    if fade and target_vol:
        _run_applescript(
            f'tell application "Spotify" to set sound volume to {min(_FADE_START_PERCENT, target_vol)}'
        )
    _run_applescript(f'tell application "Spotify" to play track "{uri}"')
    _run_applescript(
        f'tell application "Spotify" to set player position to {state["position_ms"] / 1000.0}'
    )
    if fade and target_vol:
        _fade_volume_applescript(target_vol, abort_check)
    return "status: resumed"


def search_track(query: str) -> str:
    """Search for `query` and return top 3 matching tracks in a language-neutral format."""
    query = (query or "").strip()
    if not query:
        return "status: error_no_query"

    sp = _get_client()
    try:
        cleaned_query = _clean_hebrew_query(query)
        search_query = cleaned_query if cleaned_query else query
        results = sp.search(q=search_query, type="track,episode,show", limit=5)
        
        candidates = []
        
        # 1. Tracks
        tracks = results.get("tracks", {}).get("items", [])
        for item in tracks[:3]:
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            candidates.append({
                "name": item["name"],
                "artist": artists,
                "type": "track",
                "popularity": item.get("popularity", 0),
                "uri": item["uri"]
            })
            
        # 2. Episodes
        episodes = results.get("episodes", {}).get("items", [])
        for item in episodes[:3]:
            show_name = item.get("show", {}).get("name", "Unknown Show")
            candidates.append({
                "name": item["name"],
                "artist": show_name,
                "type": "episode",
                "popularity": 0,
                "uri": item["uri"]
            })

        # 3. Shows
        shows = results.get("shows", {}).get("items", [])
        for item in shows[:3]:
            publisher = item.get("publisher", "Unknown Publisher")
            candidates.append({
                "name": item["name"],
                "artist": publisher,
                "type": "show",
                "popularity": 0,
                "uri": item["uri"]
            })
            
        # Prioritize episodes/shows if query has podcast intent
        is_podcast_intent = any(w in query.lower() for w in ("פודקאסט", "פודקסט", "podcast", "פרק", "episode", "תוכנית", "תכנית"))
        if is_podcast_intent:
            candidates.sort(key=lambda c: 0 if c["type"] in ("episode", "show") else 1)
            
        if not candidates:
            return "status: empty_results"
            
        return json.dumps(candidates[:3], ensure_ascii=False)
    except Exception as exc:
        return f"status: error_search_failed, details: {exc}"


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
            _matches_configured = (
                " [SPOTIFY_DEVICE_NAME match]"
                if SPOTIFY_DEVICE_NAME and SPOTIFY_DEVICE_NAME.lower() in _d["name"].lower()
                else ""
            )
            print(f"  - {_d['name']} ({_d['type']}){_active}{_matches_configured}")
        if not SPOTIFY_DEVICE_NAME:
            print(
                "\nNo SPOTIFY_DEVICE_NAME set -- if none of the above is ever "
                "'[active]' when you ask to play music, playback falls back to "
                "an arbitrary device from this list. Add e.g. "
                f"SPOTIFY_DEVICE_NAME={_devices[0]['name']!r} to .pi-config to "
                "pick one deliberately instead."
            )
