from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path

# Routing our own playback through the same physical device as the mic input
# (a speaker wired into the reSpeaker's 3.5mm jack) is what gives the
# XVF3800's onboard AEC a reference signal to cancel from the mic at all --
# but confirmed live, twice, on this dev Mac (once with the reSpeaker's USB
# through a dock, once connected directly to a Mac USB port -- ruling out
# dock/hub bandwidth as the cause) that simultaneous record+playback on this
# device reliably corrupts audio on macOS's CoreAudio backend specifically
# ("PaMacCore (AUHAL) err=-50", badly garbled STT, fragmented playback).
# Linux/ALSA (the actual Pi target) is a different audio backend entirely and
# may not share this limitation -- enabled there, kept off on macOS so the
# dev/demo Mac setup stays reliable rather than picking one platform to break.
#
# On macOS specifically, `None` (system default) isn't a safe fallback either
# -- confirmed live: once the reSpeaker is connected directly via USB (rather
# than through a dock), macOS itself switches its own default output device
# to it, so `None` would silently re-select the exact device we're avoiding.
# Naming the Mac's own built-in speakers explicitly sidesteps that; if this
# ever runs on a non-MacBook-Pro Mac, find_output_device() falls back to the
# system default with a printed warning rather than failing outright.
_SAME_DEVICE_OUTPUT_SUPPORTED = platform.system() == "Linux"
_MAC_SAFE_OUTPUT_HINT = "MacBook Pro Speakers"


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 44100
    channels: int = 1
    duration_seconds: float = 6.5
    dtype: str = "int16"

    # Substring match(es) (case-insensitive) used to auto-pick devices by
    # name. A tuple is tried in order, first match wins -- e.g. prefer the
    # reSpeaker mic array when it's plugged in, otherwise fall back to the
    # QuadCast. Leave as None to fall back to the system default device.
    input_name_hint: str | tuple[str, ...] | None = ("reSpeaker", "QuadCast")
    output_name_hint: str | None = "reSpeaker" if _SAME_DEVICE_OUTPUT_SUPPORTED else _MAC_SAFE_OUTPUT_HINT

    output_dir: Path = Path("recordings")
    test_filename: str = "mic_test.wav"

    @property
    def test_filepath(self) -> Path:
        return self.output_dir / self.test_filename


DEFAULT_CONFIG = AudioConfig()
