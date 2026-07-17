from __future__ import annotations

import queue
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
    hw_channels = device.max_input_channels
    frames = int(duration_seconds * sample_rate)

    audio = _record_with_fallback(device, frames, sample_rate, hw_channels)
    if hw_channels > 1:
        audio = audio[:, 0]

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(1)
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


# Empirically-tuned against this project's own mic testing (see check_mic_level.py-style
# diagnostics): background noise sat around RMS 90-160, speech spiked to 700-4000+.
# Device/room dependent -- may need retuning for a different mic (e.g. the Pi's).
SILENCE_RMS_THRESHOLD = 250.0
CHUNK_SAMPLES = 1600  # 100ms at 16kHz


def record_until_silence(
    device: Device,
    filepath: Path,
    sample_rate: int,
    channels: int = 1,
    *,
    initial_timeout: float = 4.0,
    silence_duration: float = 1.2,
    max_seconds: float = 15.0,
    lead_in_seconds: float = 0.0,
) -> Path | None:
    """Record until the speaker falls silent, instead of a fixed duration.

    Cuts dead air (no more waiting out a fixed window after the speaker's
    already done) and avoids clipping the start of what they say (recording
    starts immediately, not after some other fixed-duration step finishes).

    `lead_in_seconds` buffers that much audio from stream-open without
    running silence detection on it, then folds it into the recording once
    real speech is detected right after -- meant to cover a concurrently
    playing ack chime, so someone who starts talking before the chime ends
    isn't clipped, while the chime's own sound doesn't get mistaken for
    speech (see wake_word_daemon.py's caller).

    Returns None (and writes no file) if no speech is detected at all within
    `initial_timeout` -- lets callers distinguish "they said something and
    finished" from "they didn't say anything," e.g. for deciding whether a
    multi-turn conversation has ended.
    """
    channels = device.max_input_channels
    audio_queue: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        audio_queue.put(indata[:, 0].copy())

    chunks: list[np.ndarray] = []
    lead_in_buffer: list[np.ndarray] = []
    speech_started = False
    silence_elapsed = 0.0
    elapsed = 0.0
    lead_in_elapsed = 0.0
    chunk_duration = CHUNK_SAMPLES / sample_rate

    with sd.InputStream(
        device=device.index,
        channels=channels,
        samplerate=sample_rate,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
        latency='high',
        callback=callback,
    ):
        while elapsed < max_seconds:
            chunk = audio_queue.get()

            if lead_in_elapsed < lead_in_seconds:
                lead_in_elapsed += chunk_duration
                lead_in_buffer.append(chunk)
                continue

            elapsed += chunk_duration
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))

            if rms > SILENCE_RMS_THRESHOLD:
                if not speech_started and lead_in_buffer:
                    chunks.extend(lead_in_buffer)
                    lead_in_buffer = []
                speech_started = True
                silence_elapsed = 0.0
            elif speech_started:
                silence_elapsed += chunk_duration

            if speech_started:
                chunks.append(chunk)
                if silence_elapsed >= silence_duration:
                    break
            elif elapsed >= initial_timeout:
                return None

    if not chunks:
        return None

    audio = np.concatenate(chunks)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())

    return filepath
