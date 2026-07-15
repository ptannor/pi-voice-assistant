from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from .devices import Device
from .errors import PlaybackFailed


def _load_wav(filepath: Path) -> tuple[np.ndarray, int]:
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
    else:
        # Convert mono (1 channel) to stereo (2 channels) to prevent static noise
        # from low-quality driver-level mono-to-stereo emulation on DACs/speakers.
        audio = np.column_stack((audio, audio))
    return audio, sample_rate


def play_wav(filepath: Path, device: Device) -> None:
    audio, sample_rate = _load_wav(filepath)
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


def play_wav_async(filepath: Path, device: Device) -> None:
    """Fire-and-forget playback -- starts the sound and returns immediately
    instead of waiting for it to finish. For a short acknowledgment tone
    (e.g. "still working on it") that shouldn't add its own latency on top
    of whatever's happening concurrently, like a slow tool call in flight.
    """
    audio, sample_rate = _load_wav(filepath)
    try:
        sd.play(audio, samplerate=sample_rate, device=device.index)
    except sd.PortAudioError as exc:
        raise PlaybackFailed(f"Playback failed on '{device.name}' at {sample_rate} Hz: {exc}.") from exc
    except PermissionError as exc:
        raise PlaybackFailed(
            "Permission denied opening the speaker. On Raspberry Pi OS, "
            "add your user to the audio group and re-login: "
            "'sudo usermod -aG audio $USER'"
        ) from exc
