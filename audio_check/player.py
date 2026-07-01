from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from .devices import Device
from .errors import PlaybackFailed


def play_wav(filepath: Path, device: Device) -> None:
    if not filepath.exists():
        raise PlaybackFailed(f"No WAV file at {filepath}. Record one first.")

    with wave.open(str(filepath), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sample_width)
    if dtype is None:
        raise PlaybackFailed(f"Unsupported WAV sample width: {sample_width} bytes")

    audio = np.frombuffer(raw, dtype=dtype)
    if channels > 1:
        audio = audio.reshape(-1, channels)

    try:
        sd.play(audio, samplerate=sample_rate, device=device.index)
        sd.wait()
    except sd.PortAudioError as exc:
        raise PlaybackFailed(
            f"Playback failed on '{device.name}' at {sample_rate} Hz: {exc}. "
            "The speaker may not support this sample rate, or the wrong "
            "output was selected via 'aplay -l' / raspi-config."
        ) from exc
    except PermissionError as exc:
        raise PlaybackFailed(
            "Permission denied opening the speaker. On Raspberry Pi OS, "
            "add your user to the audio group and re-login: "
            "'sudo usermod -aG audio $USER'"
        ) from exc
