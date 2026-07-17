"""LED ring patterns for the reSpeaker XVF3800 4-Mic Array.

Driven by the `xvf_host` USB control tool that ships with the array (see
README's Mic LED patterns section for where to get the binary). The device's
LED protocol only exposes 5 fixed effects (off/breath/rainbow/single-color/
doa) plus a doa base+highlight color pair -- there's no per-pixel/custom-
animation command, so a moving "comet" isn't possible; these patterns are
the closest fit confirmed live against the real hardware:

- idle: resting look, static rainbow across the ring
- listening: wake word just fired, actively recording the question -- blue
  base with a green highlight in the direction the sound is coming from
  (LED_DOA_COLOR reused with custom colors instead of the device default)
- speaking: the assistant's reply is playing (solid magenta)
- idle transition: a brief white flash when a conversation ends, then back
  to the idle rainbow
- error: something is stopping the assistant from working (no wifi, a
  failed API call, no mic/speaker found) -- solid orange, held rather than
  timed out, since the underlying problem may still be there

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
EFFECT_DOA = 4

IDLE_EFFECT = EFFECT_RAINBOW  # resting look

LISTENING_BASE_COLOR = 0x0033FF  # blue -- ring base while recording the question
LISTENING_DOA_COLOR = 0x00FF00  # green -- highlight in the direction of the sound
SPEAKING_COLOR = 0xFF00FF  # magenta -- assistant is talking
TRANSITION_COLOR = 0xFFFFFF  # white flash -- wrapping up, heading back to idle
TRANSITION_SECONDS = 0.5  # how long the flash holds before settling back to idle
ERROR_COLOR = 0xFF8800  # orange -- reserved for wifi/API/hardware trouble
BRIGHTNESS = 255
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


def _apply_solid(color: int) -> None:
    _run("led_effect", str(EFFECT_SOLID))
    _run("led_color", f"0x{color:06x}")
    _run("led_brightness", str(BRIGHTNESS))
    _run("led_gammify", "1")  # gamma-correct so the color reads as vivid, not washed out


def _apply_doa(base_color: int, doa_color: int) -> None:
    _run("led_effect", str(EFFECT_DOA))
    _run("led_doa_color", f"0x{base_color:06x}", f"0x{doa_color:06x}")
    _run("led_brightness", str(BRIGHTNESS))
    _run("led_gammify", "1")


def enter_idle() -> None:
    """Resting state: static rainbow."""
    _bump_generation()
    threading.Thread(target=lambda: _run("led_effect", str(IDLE_EFFECT)), daemon=True).start()


def enter_listening() -> None:
    """Wake word just fired -- actively recording the user's question."""
    _bump_generation()
    threading.Thread(
        target=lambda: _apply_doa(LISTENING_BASE_COLOR, LISTENING_DOA_COLOR), daemon=True
    ).start()


def enter_thinking() -> None:
    """The assistant is processing the query (rainbow animation)."""
    _bump_generation()
    threading.Thread(target=lambda: _run("led_effect", str(EFFECT_RAINBOW)), daemon=True).start()


def enter_speaking() -> None:
    """The assistant's spoken reply is playing."""
    _bump_generation()
    threading.Thread(target=lambda: _apply_solid(SPEAKING_COLOR), daemon=True).start()


def enter_error() -> None:
    """Something is stopping the assistant from working -- held solid orange
    rather than timing back out to idle, since the underlying problem (no
    wifi, a failed API call, missing hardware) may still be there. Whatever
    next calls one of the other `enter_*` functions clears it."""
    _bump_generation()
    threading.Thread(target=lambda: _apply_solid(ERROR_COLOR), daemon=True).start()


def enter_idle_transition() -> None:
    """Conversation just ended -- brief white flash, then back to `enter_idle`.

    Guarded by a generation token so that if listening/speaking starts again
    during the flash (e.g. a fresh wake word right after goodbye), this
    stale transition's delayed restore-to-idle step is skipped instead of
    clobbering the newer state.
    """
    gen = _bump_generation()

    def run() -> None:
        _apply_solid(TRANSITION_COLOR)
        time.sleep(TRANSITION_SECONDS)
        with _generation_lock:
            current = _generation
        if gen == current:
            _run("led_effect", str(IDLE_EFFECT))

    threading.Thread(target=run, daemon=True).start()
