from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from .devices import Device
from .errors import PlaybackFailed

# sounddevice.play()/wait() manage a single *global* default output stream,
# not one independent per call -- concurrent calls from different threads
# race on that shared state instead of queueing cleanly. Confirmed: the
# timer alarm's looping play_wav() (brain/timer.py) and the wake-word
# daemon's ack-chime play_wav() (wake_word_daemon.py, played on a separate
# thread so recording can start immediately) collided this way and hung
# forever -- stuck in native PortAudio code, so not even interruptible by
# Ctrl-C. This lock serializes play_wav() calls across threads so a second
# caller waits its turn instead of corrupting the first's in-flight stream.
# Deliberately not applied to play_wav_async(), which is fire-and-forget by
# design and expected to layer over/interrupt whatever's already playing.
_playback_lock = threading.Lock()


def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    num_channels = audio.shape[1] if audio.ndim > 1 else 1
    duration = len(audio) / orig_sr
    num_target_samples = int(duration * target_sr)
    
    orig_x = np.arange(len(audio))
    target_x = np.linspace(0, len(audio) - 1, num_target_samples)
    
    if audio.ndim > 1:
        resampled_channels = []
        for c in range(num_channels):
            resampled_c = np.interp(target_x, orig_x, audio[:, c])
            resampled_channels.append(resampled_c)
        return np.column_stack(resampled_channels).astype(audio.dtype)
    else:
        resampled = np.interp(target_x, orig_x, audio)
        return resampled.astype(audio.dtype)


def _load_wav(filepath: Path, target_sr: int | None = None) -> tuple[np.ndarray, int]:
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

    if target_sr is not None and sample_rate != target_sr:
        audio = resample_audio(audio, sample_rate, target_sr)
        sample_rate = target_sr

    return audio, sample_rate


def play_wav(filepath: Path, device: Device) -> None:
    target_sr = int(device.default_samplerate)
    audio, sample_rate = _load_wav(filepath, target_sr=target_sr)
    try:
        with _playback_lock:
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
    target_sr = int(device.default_samplerate)
    audio, sample_rate = _load_wav(filepath, target_sr=target_sr)
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
