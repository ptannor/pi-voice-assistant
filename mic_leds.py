"""LED ring patterns for the reSpeaker XVF3800 4-Mic Array.

Out of the box the array runs LED_EFFECT 4 ("doa"): a blue voice-activity
trace with a brighter spot in the direction of arrival. That's left as the
resting/idle look. This module adds three distinct patterns on top of it,
driven by the `xvf_host` USB control tool that ships with the array (see
README's Mic LED patterns section for where to get the binary):

- listening: wake word just fired, actively recording the question
- speaking: the assistant's reply is playing
- idle transition: a brief flourish when a conversation ends, then back to
  the default "doa" idle look

All calls are fire-and-forget (background thread, short subprocess timeout)
so a missing/disconnected array never blocks or crashes the voice pipeline --
this is a cosmetic layer, not a dependency of it.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

EFFECT_OFF = 0
EFFECT_BREATH = 1
EFFECT_RAINBOW = 2
EFFECT_SOLID = 3
EFFECT_DOA = 4  # device default -- the resting/idle look

# Distinguishable from the idle blue/green DOA trace and from each other.
LISTENING_COLOR = 0x9B30FF  # violet -- "I'm actively listening for your question"
LISTENING_SPEED = 3  # fast breath: urgent/attentive
SPEAKING_COLOR = 0xFF8800  # amber, solid (no motion) -- "I'm talking"
TRANSITION_SPEED = 4  # fast rainbow sweep
TRANSITION_SECONDS = 0.7  # how long the sweep plays before settling back to doa
BRIGHTNESS = 220
_SUBPROCESS_TIMEOUT = 2.0

_PLATFORM_DIRS = {
    ("Darwin", "arm64"): "mac_arm64",
    ("Linux", "aarch64"): "rpi_64bit",
    ("Linux", "arm64"): "rpi_64bit",
    ("Linux", "x86_64"): "linux_x86_64",
}

_generation_lock = threading.Lock()
_generation = 0
_warned_missing = False


def _bump_generation() -> int:
    global _generation
    with _generation_lock:
        _generation += 1
        return _generation


def _binary_path() -> Path | None:
    override = os.environ.get("XVF_HOST_BIN")
    if override:
        return Path(override)
    platform_dir = _PLATFORM_DIRS.get((platform.system(), platform.machine()))
    if platform_dir is None:
        return None
    return Path(__file__).parent / "vendor" / "xvf_host" / platform_dir / "xvf_host"


def _run(*args: str) -> bool:
    global _warned_missing
    binary = _binary_path()
    if binary is None or not binary.exists():
        if not _warned_missing:
            print(
                f"mic_leds: xvf_host binary not found at {binary} -- "
                "LED patterns disabled (see README's Mic LED patterns section)",
                file=sys.stderr,
                flush=True,
            )
            _warned_missing = True
        return False
    try:
        subprocess.run(
            [str(binary), *args],
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=True,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"mic_leds: {' '.join(args)} failed: {exc}", file=sys.stderr, flush=True)
        return False


def _apply_breath(color: int, speed: int) -> None:
    _run("led_effect", str(EFFECT_BREATH))
    _run("led_color", f"0x{color:06x}")
    _run("led_speed", str(speed))
    _run("led_brightness", str(BRIGHTNESS))


def _apply_solid(color: int) -> None:
    _run("led_effect", str(EFFECT_SOLID))
    _run("led_color", f"0x{color:06x}")
    _run("led_brightness", str(BRIGHTNESS))


def enter_idle() -> None:
    """Resting state: the array's own default DOA trace."""
    _bump_generation()
    threading.Thread(target=lambda: _run("led_effect", str(EFFECT_DOA)), daemon=True).start()


def enter_listening() -> None:
    """Wake word just fired -- actively recording the user's question."""
    _bump_generation()
    threading.Thread(
        target=lambda: _apply_breath(LISTENING_COLOR, LISTENING_SPEED), daemon=True
    ).start()


def enter_speaking() -> None:
    """The assistant's spoken reply is playing."""
    _bump_generation()
    threading.Thread(target=lambda: _apply_solid(SPEAKING_COLOR), daemon=True).start()


def enter_idle_transition() -> None:
    """Conversation just ended -- brief flourish, then back to `enter_idle`.

    Guarded by a generation token so that if listening/speaking starts again
    during the flourish (e.g. a fresh wake word right after goodbye), this
    stale transition's delayed restore-to-idle step is skipped instead of
    clobbering the newer state.
    """
    gen = _bump_generation()

    def run() -> None:
        _run("led_effect", str(EFFECT_RAINBOW))
        _run("led_speed", str(TRANSITION_SPEED))
        time.sleep(TRANSITION_SECONDS)
        with _generation_lock:
            current = _generation
        if gen == current:
            _run("led_effect", str(EFFECT_DOA))

    threading.Thread(target=run, daemon=True).start()
