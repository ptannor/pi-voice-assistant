"""Cross-platform speaker volume control (macOS dev machine + Pi/PipeWire).

Exposed as an internal 0-10 scale to Claude (matching set_volume's existing
tool schema) mapped onto the OS's real 0-100% volume -- one "level" is 10%.

macOS: `osascript`/AppleScript "output volume" (same subprocess pattern
brain/spotify.py already uses for local Spotify control via AppleScript).
Linux (the Pi): `pactl` against the default sink -- PipeWire (already this
project's audio backend, see wake_word_daemon.py's PipeWire comments)
provides a PulseAudio-compatible `pactl` interface, so no extra dependency.
"""
from __future__ import annotations

import platform
import subprocess

_STEP_LEVELS = 1  # volume_up/volume_down move by one level (10%) per call
_TIMEOUT_SECONDS = 5


class VolumeError(Exception):
    pass


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _get_percent() -> int:
    try:
        if _is_macos():
            result = subprocess.run(
                ["osascript", "-e", "output volume of (get volume settings)"],
                capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=True,
            )
            return int(result.stdout.strip())
        result = subprocess.run(
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=True,
        )
        # e.g. "Volume: front-left: 45875 /  70% / ..." -- take the first percentage found
        for token in result.stdout.split():
            if token.endswith("%"):
                return int(token[:-1])
        raise VolumeError(f"Couldn't parse pactl output: {result.stdout!r}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, FileNotFoundError) as exc:
        raise VolumeError(f"Couldn't read current volume: {exc}") from exc


def _set_percent(pct: int) -> None:
    pct = max(0, min(100, pct))
    try:
        if _is_macos():
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {pct}"],
                capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=True,
            )
        else:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
                capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=True,
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise VolumeError(f"Couldn't set volume: {exc}") from exc


def _level_from_percent(pct: int) -> int:
    return round(pct / 10)


def volume_up() -> str:
    current = _get_percent()
    new_pct = min(100, current + _STEP_LEVELS * 10)
    _set_percent(new_pct)
    return f"status: ok, volume: {_level_from_percent(new_pct)}"


def volume_down() -> str:
    current = _get_percent()
    new_pct = max(0, current - _STEP_LEVELS * 10)
    _set_percent(new_pct)
    return f"status: ok, volume: {_level_from_percent(new_pct)}"


def set_volume(level: int) -> str:
    level = max(0, min(10, level))
    _set_percent(level * 10)
    return f"status: ok, volume: {level}"
