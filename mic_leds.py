"""LED ring patterns for the reSpeaker XVF3800 4-Mic Array.

Driven by the `xvf_host` USB control tool that ships with the array (see
README's Mic LED patterns section for where to get the binary). The device's
LED protocol only exposes 5 fixed effects (off/breath/rainbow/single-color/
doa) plus a doa base+highlight color pair -- there's no per-pixel/custom-
animation command, so a moving "comet" isn't possible; these patterns are
the closest fit confirmed live against the real hardware.

Rainbow mode's rotation is real but oddly fragile: `xvf_host` re-opens a
fresh USB connection for every single invocation (see the "Device
(USB)::device_init()" line it prints each time), and issuing
led_effect/led_speed/led_brightness/led_gammify as separate invocations --
exactly how every other effect below is applied -- left the rainbow
completely static regardless of LED_SPEED's value. Confirmed live it only
actually rotates when all of those commands run inside one *continuous*
session via `xvf_host -e <command-list-file>` instead (see _run_batch) --
reconnecting between commands seems to reset whatever's driving the
rotation. Breath mode's own speed doesn't have this problem (LISTENING/
THINKING/ERROR were all tuned live using ordinary separate invocations,
same as before), so only _apply_idle_effect uses the batched path.

- idle: resting look, rainbow slowly rotating around the ring (speed 8,
  confirmed live)
- listening: wake word just fired, actively recording the question -- blue
  base with a green highlight in the direction the sound is coming from
  (LED_DOA_COLOR reused with custom colors instead of the device default)
- thinking: recording just finished, waiting on transcription/Claude/TTS --
  white breathing pulse (speed tuned live against the real hardware: 1 was
  too slow, 4 too fast, 2 confirmed good)
- speaking: the assistant's reply is playing (solid magenta)
- idle transition: a brief white flash when a conversation ends, then back
  to the idle rainbow
- error: something is stopping the assistant from working (no wifi, a
  failed API call, no mic/speaker found) -- orange breathing pulse, held
  rather than timed out, since the underlying problem may still be there.
  If it was actually a network outage, a background watch (see
  _start_recovery_watch) clears it on its own once connectivity is back,
  rather than requiring some unrelated fresh interaction (a wake word) to
  do that as a side effect -- confirmed live: wifi recovered on its own
  well before anyone next spoke to it, and the light just stayed orange
  the whole time regardless.

All calls are fire-and-forget (background thread, short subprocess timeout)
so a missing/disconnected array never blocks or crashes the voice pipeline --
this is a cosmetic layer, not a dependency of it.
"""
from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

EFFECT_OFF = 0
EFFECT_BREATH = 1
EFFECT_RAINBOW = 2
EFFECT_SOLID = 3
EFFECT_DOA = 4

IDLE_EFFECT = EFFECT_RAINBOW  # resting look
IDLE_SPEED = 8  # confirmed live: 2 too slow, 10 too fast, 8 just right

LISTENING_BASE_COLOR = 0x0033FF  # blue -- ring base while recording the question
LISTENING_DOA_COLOR = 0x00FF00  # green -- highlight in the direction of the sound
THINKING_COLOR = 0xFFFFFF  # white breathing pulse -- waiting on transcribe/Claude/TTS
THINKING_SPEED = 2  # confirmed live: 1 too slow, 4 too fast, 2 just right
SPEAKING_COLOR = 0xFF00FF  # magenta -- assistant is talking
TRANSITION_COLOR = 0xFFFFFF  # white flash -- wrapping up, heading back to idle
TRANSITION_SECONDS = 0.5  # how long the flash holds before settling back to idle
ERROR_COLOR = 0xFF8800  # orange -- reserved for wifi/API/hardware trouble
BRIGHTNESS = 255
_SUBPROCESS_TIMEOUT = 2.0
# How often enter_error()'s recovery watch retries a connectivity check --
# frequent enough to clear the light soon after wifi actually comes back,
# cheap enough (one fast TCP connect attempt) to poll indefinitely for as
# long as an error happens to persist.
_RECOVERY_CHECK_SECONDS = 15.0
# 8.8.8.8:53 (Google public DNS) -- picked only for being a well-known,
# highly-available anycast IP that answers a bare TCP connect fast; this
# isn't a DNS lookup or a real request to Google, just "can this machine
# reach anything on the internet at all," which needs no host/site of its
# own to stay up.
_CONNECTIVITY_CHECK_HOST = ("8.8.8.8", 53)

_PLATFORM_DIRS = {
    ("Darwin", "arm64"): "mac_arm64",
    ("Linux", "aarch64"): "rpi_64bit",
    ("Linux", "arm64"): "rpi_64bit",
    ("Linux", "x86_64"): "linux_x86_64",
}

_generation_lock = threading.Lock()
_generation = 0
_warned_missing = False
# Every enter_* function below fires its LED sequence on its own throwaway
# background thread (fire-and-forget -- see module docstring), with no
# synchronization between them. Two of those threads hitting the physical
# USB device at the same moment (e.g. a wake-word interrupt re-triggering
# enter_listening() while the previous state's own sequence is still
# mid-flight) previously raced on the same USB handle -- confirmed live as
# an intermittent "led_gammify 1 failed: ... exit status 8" from xvf_host,
# not any real hardware/driver problem. This lock serializes each apply_*
# sequence as one atomic unit (same reasoning as audio_check/player.py's
# own _playback_lock for the analogous output-stream contention).
_led_lock = threading.Lock()


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


def _require_binary() -> Path | None:
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
        return None
    return binary


def _run(*args: str) -> bool:
    binary = _require_binary()
    if binary is None:
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


def _run_batch(commands: list[str]) -> bool:
    """Same role as _run(), but issues all `commands` (each a full "command
    arg [arg...]" line, same names/argument formats _run() takes) within
    one continuous USB session via xvf_host's -e/--execute-command-list,
    instead of one subprocess invocation -- and one fresh USB
    reconnection -- per command. See the module docstring: only this batched
    form actually rotates rainbow mode; reconnecting between commands (what
    every separate _run() call does) resets whatever drives that rotation.
    """
    binary = _require_binary()
    if binary is None:
        return False
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(commands) + "\n")
            path = f.name
        subprocess.run(
            [str(binary), "-e", path],
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=True,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"mic_leds: batch {commands} failed: {exc}", file=sys.stderr, flush=True)
        return False
    finally:
        if path is not None:
            Path(path).unlink(missing_ok=True)


def _apply_solid(color: int) -> None:
    with _led_lock:
        _run("led_effect", str(EFFECT_SOLID))
        _run("led_color", f"0x{color:06x}")
        _run("led_brightness", str(BRIGHTNESS))
        _run("led_gammify", "1")  # gamma-correct so the color reads as vivid, not washed out


def _apply_doa(base_color: int, doa_color: int) -> None:
    with _led_lock:
        _run("led_effect", str(EFFECT_DOA))
        _run("led_doa_color", f"0x{base_color:06x}", f"0x{doa_color:06x}")
        _run("led_brightness", str(BRIGHTNESS))
        _run("led_gammify", "1")


def _apply_breath(color: int, speed: int) -> None:
    with _led_lock:
        _run("led_effect", str(EFFECT_BREATH))
        _run("led_color", f"0x{color:06x}")
        _run("led_speed", str(speed))
        _run("led_brightness", str(BRIGHTNESS))
        _run("led_gammify", "1")


def _apply_idle_effect() -> None:
    with _led_lock:
        _run_batch([
            f"led_effect {IDLE_EFFECT}",
            f"led_speed {IDLE_SPEED}",
            f"led_brightness {BRIGHTNESS}",
            "led_gammify 1",
        ])


def enter_idle() -> None:
    """Resting state: rainbow, slowly rotating around the ring."""
    _bump_generation()
    threading.Thread(target=_apply_idle_effect, daemon=True).start()


def enter_listening() -> None:
    """Wake word just fired -- actively recording the user's question."""
    _bump_generation()
    threading.Thread(
        target=lambda: _apply_doa(LISTENING_BASE_COLOR, LISTENING_DOA_COLOR), daemon=True
    ).start()


def enter_thinking() -> None:
    """Recording just finished -- waiting on transcription/Claude/TTS."""
    _bump_generation()
    threading.Thread(
        target=lambda: _apply_breath(THINKING_COLOR, THINKING_SPEED), daemon=True
    ).start()


def enter_speaking() -> None:
    """The assistant's spoken reply is playing."""
    _bump_generation()
    threading.Thread(target=lambda: _apply_solid(SPEAKING_COLOR), daemon=True).start()


def enter_error() -> None:
    """Something is stopping the assistant from working -- orange breathing
    pulse (same pattern/speed as `enter_thinking`, just orange instead of
    white) held rather than timing back out to idle, since the underlying
    problem (no wifi, a failed API call, missing hardware) may still be
    there. A static solid color reads as "off/stuck" rather than "still
    going, something's wrong" -- breathing matches how the rest of this
    module signals "the assistant is still alive" while working. Cleared
    either by whatever next calls one of the other `enter_*` functions (a
    fresh wake word, most commonly), or on its own once network
    connectivity is confirmed back (see _start_recovery_watch) -- most
    real errors here trace back to a dropped connection, and that shouldn't
    need someone to notice and speak to it just to turn the light off."""
    gen = _bump_generation()
    threading.Thread(
        target=lambda: _apply_breath(ERROR_COLOR, THINKING_SPEED), daemon=True
    ).start()
    _start_recovery_watch(gen)


def _network_reachable() -> bool:
    try:
        socket.create_connection(_CONNECTIVITY_CHECK_HOST, timeout=2.0).close()
        return True
    except OSError:
        return False


def _start_recovery_watch(gen: int) -> None:
    """Started fresh from every enter_error() call (not one persistent
    background thread) -- polls for connectivity and, once it's back, clears
    the error state on its own. Guarded by the same generation token every
    other enter_* transition uses: if something else (a fresh wake word, a
    newer error) has since changed the state, `gen` is stale by the time
    this notices the network is back, and it exits without touching
    anything instead of clobbering whatever's actually current.

    Not gated on the error actually being network-related -- there's no way
    to tell that from here, and there doesn't need to be: if the real cause
    was something else, the light going back to idle a little early just
    means the next real failure lights it up again, same as always.
    """

    def watch() -> None:
        while True:
            time.sleep(_RECOVERY_CHECK_SECONDS)
            with _generation_lock:
                if _generation != gen:
                    return
            if _network_reachable():
                with _generation_lock:
                    if _generation != gen:
                        return
                enter_idle()
                return

    threading.Thread(target=watch, daemon=True).start()


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
            _apply_idle_effect()

    threading.Thread(target=run, daemon=True).start()
