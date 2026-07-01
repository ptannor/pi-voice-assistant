from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from .devices import Device
from .errors import RecordingFailed


def record_to_wav(
    device: Device,
    filepath: Path,
    duration_seconds: float,
    sample_rate: int,
    channels: int,
) -> Path:
    channels = min(channels, device.max_input_channels) or 1
    frames = int(duration_seconds * sample_rate)

    audio = _record_with_fallback(device, frames, sample_rate, channels)

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # int16 -> 2 bytes/sample
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())

    return filepath


def _record_with_fallback(
    device: Device, frames: int, sample_rate: int, channels: int
) -> np.ndarray:
    try:
        return _do_record(device, frames, sample_rate, channels)
    except sd.PortAudioError as exc:
        fallback_rate = int(device.default_samplerate)
        if fallback_rate == sample_rate:
            raise RecordingFailed(
                f"Recording failed at {sample_rate} Hz on '{device.name}': {exc}"
            ) from exc
        print(
            f"Warning: {sample_rate} Hz not supported by '{device.name}', "
            f"retrying at its default rate ({fallback_rate} Hz)."
        )
        fallback_frames = int(frames * fallback_rate / sample_rate)
        try:
            return _do_record(device, fallback_frames, fallback_rate, channels)
        except sd.PortAudioError as retry_exc:
            raise RecordingFailed(
                f"Recording failed on '{device.name}' at both {sample_rate} Hz "
                f"and its default {fallback_rate} Hz: {retry_exc}"
            ) from retry_exc
    except PermissionError as exc:
        raise RecordingFailed(
            "Permission denied opening the microphone. On Raspberry Pi OS, "
            "add your user to the audio group and re-login: "
            "'sudo usermod -aG audio $USER'"
        ) from exc


def _do_record(device: Device, frames: int, sample_rate: int, channels: int) -> np.ndarray:
    audio = sd.rec(
        frames,
        samplerate=sample_rate,
        channels=channels,
        dtype="int16",
        device=device.index,
    )
    sd.wait()
    return audio
