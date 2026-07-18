from __future__ import annotations

import queue
import time
import wave
from pathlib import Path
from typing import Callable

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


# Empirically-tuned against this project's own mic testing (see check_mic_level.py-style
# diagnostics): background noise sat around RMS 90-160, speech spiked to 700-4000+.
# Device/room dependent -- may need retuning for a different mic (e.g. the Pi's).
SILENCE_RMS_THRESHOLD = 250.0
CHUNK_SAMPLES = 1600  # 100ms at 16kHz
# A single loud 100ms chunk isn't necessarily the user talking again -- could
# be one syllable of background chatter, a clink, a door. Require a run this
# long over threshold before treating it as renewed speech that resets the
# silence countdown below; a brief blip shorter than this is just ignored
# (still recorded, just doesn't interrupt the countdown toward ending the
# turn). Confirmed needed: in a noisy room, background conversation kept
# resetting the countdown on every interjection, so a short command ("stop",
# "play X") never hit its silence cutoff and dragged in unrelated speech
# recorded well past it. 0.3s is well under a real spoken syllable/word but
# comfortably longer than an isolated one-chunk noise spike.
MIN_SPEECH_RUN_SECONDS = 0.3
# Separate, shorter threshold for confirming the *onset* of speech (the very
# first sustained loud run of the recording) rather than renewed speech
# after a pause -- a curt, one-syllable command like "stop" can have a total
# loud duration close to or under MIN_SPEECH_RUN_SECONDS, especially with a
# quiet trailing consonant, so confirmed live: using the same 0.3s threshold
# for onset sometimes never confirmed "stop" as speech at all, producing a
# short garbled fragment instead ("Tap."). Kept lower than
# MIN_SPEECH_RUN_SECONDS specifically because the onset case doesn't carry
# the same false-positive risk the mid-conversation case does: background
# noise ahead of the wake-word response is far less likely at the exact
# moment someone just finished talking to the device than mid-pause during
# an ongoing turn, so it can afford to be more sensitive.
MIN_SPEECH_ONSET_SECONDS = 0.2

# macOS CoreAudio's AUHAL backend occasionally fails to start a stream with
# "Internal PortAudio error" (-9986) -- confirmed live, right after a dense
# burst of conversation turns (many stream opens/closes in quick
# succession). Transient: a brief pause is usually enough for it to settle,
# so one retry avoids losing the whole turn (and needing a fresh wake word)
# to what's typically a passing hiccup rather than a real device problem.
INPUT_STREAM_OPEN_RETRIES = 1
INPUT_STREAM_RETRY_DELAY_SECONDS = 0.2


def _open_input_stream(device: Device, channels: int, sample_rate: int, callback) -> sd.InputStream:
    attempt = 0
    while True:
        stream = None
        try:
            stream = sd.InputStream(
                device=device.index,
                channels=channels,
                samplerate=sample_rate,
                dtype="int16",
                blocksize=CHUNK_SAMPLES,
                latency='high',
                callback=callback,
            )
            stream.start()
            return stream
        except sd.PortAudioError:
            if stream is not None:
                # The constructor above already opens a real PortAudio stream
                # handle (Pa_OpenStream) before start() is ever called -- a
                # failed start() still leaves that handle allocated. Close it
                # before retrying or raising, or it leaks and contends with
                # (and can corrupt) every stream subsequently opened on this
                # device for the rest of the process's life. Confirmed this
                # was the real cause of a much worse-sounding regression: not
                # occasional garbled audio, but corrupted-sounding captures on
                # nearly every turn once a single retry had occurred.
                stream.close()
            if attempt >= INPUT_STREAM_OPEN_RETRIES:
                raise
            attempt += 1
            time.sleep(INPUT_STREAM_RETRY_DELAY_SECONDS)


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
    preroll_chunks: list[np.ndarray] | None = None,
    on_chunk: Callable[[np.ndarray], None] | None = None,
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

    `preroll_chunks`, if given, is audio already captured *before this stream
    even existed* -- e.g. wake_word_daemon.py's short rolling buffer of
    whatever was said right up to the moment a wake word fired. Unlike
    lead_in_seconds, this isn't blindly kept -- it may or may not actually
    contain speech, so it's run through the same onset-confirmation check as
    live audio before the stream opens. This closes a different gap than
    lead_in_seconds: a command said with no pause right after the wake word
    (e.g. "Alexa, stop" in one breath) would otherwise fall entirely between
    the wake-word stream closing and this one opening, never captured by
    either.

    Returns None (and writes no file) if no speech is detected at all within
    `initial_timeout` -- lets callers distinguish "they said something and
    finished" from "they didn't say anything," e.g. for deciding whether a
    multi-turn conversation has ended.

    `on_chunk`, if given, is called with every live chunk pulled from this
    stream (not `preroll_chunks` -- those predate the stream and would just
    replay whatever already-known trigger put them there) before any silence
    detection runs on it. It exists so a caller can feed the same audio to a
    wake-word model without a second, competing input stream on the same
    device -- see wake_word_daemon.py's mid-recording restart handling. It's
    expected to raise (e.g. WakeWordInterrupt) to abort the recording early;
    the stream is still closed via the `finally` below either way.
    """
    channels = min(channels, device.max_input_channels) or 1
    audio_queue: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        audio_queue.put(indata[:, 0].copy())

    chunks: list[np.ndarray] = []
    lead_in_buffer: list[np.ndarray] = []
    # Chunks in the current run of consecutive loud chunks, *before* that run
    # has reached min_speech_run_chunks and is confirmed as real speech --
    # without this, those chunks (up to MIN_SPEECH_RUN_SECONDS worth, ~0.3s)
    # were silently dropped entirely: not part of lead_in_buffer (that
    # window had already closed) and not yet appended to `chunks` (only
    # happens once speech_started is already True). Confirmed live: this
    # clipped the first word of nearly every utterance, in both languages
    # ("מה" from "מה המזג אוויר מחר", "what's" from "what's the weather").
    pending_speech_buffer: list[np.ndarray] = []
    speech_started = False
    silence_elapsed = 0.0
    elapsed = 0.0
    lead_in_elapsed = 0.0
    chunk_duration = CHUNK_SAMPLES / sample_rate
    consecutive_loud_chunks = 0
    min_speech_run_chunks = max(1, round(MIN_SPEECH_RUN_SECONDS / chunk_duration))
    min_onset_chunks = max(1, round(MIN_SPEECH_ONSET_SECONDS / chunk_duration))

    # Preroll is audio already captured before this stream even existed, so
    # it's processed before opening one -- doesn't compete with max_seconds/
    # initial_timeout, since none of that time has actually elapsed since
    # this call started. Run through the same onset-confirmation logic as
    # live audio below (not blindly kept like lead_in_seconds), sharing the
    # same consecutive_loud_chunks/pending_speech_buffer/speech_started state
    # so a loud run that started here and continues into live audio is
    # tracked continuously, not reset.
    for chunk in preroll_chunks or []:
        rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        is_loud = rms > SILENCE_RMS_THRESHOLD
        consecutive_loud_chunks = consecutive_loud_chunks + 1 if is_loud else 0

        if speech_started:
            # Once onset is confirmed (below), every remaining preroll chunk
            # must be kept unconditionally, same as the live loop's own
            # `if speech_started: chunks.append(chunk)` -- a brief quiet dip
            # here (e.g. the natural gap between the wake word and the next
            # word) must NOT be able to wipe audio that's already part of a
            # continuous, confirmed utterance. Feeding it back through the
            # is_loud-gated onset logic below was exactly that bug: confirmed
            # live, a quiet chunk between "Alexa" and "play" during preroll
            # cleared pending_speech_buffer before the rest of "play me the
            # song" could be flushed into `chunks`, silently dropping it.
            chunks.append(chunk)
            continue

        if is_loud:
            pending_speech_buffer.append(chunk)
        else:
            pending_speech_buffer = []
        if consecutive_loud_chunks >= min_onset_chunks:
            chunks.extend(pending_speech_buffer)
            pending_speech_buffer = []
            speech_started = True

    stream = _open_input_stream(device, channels, sample_rate, callback)
    try:
        while elapsed < max_seconds:
            chunk = audio_queue.get()
            if on_chunk is not None:
                on_chunk(chunk)

            # Once real speech is confirmed (from preroll above, or from a
            # live onset below), the lead-in window no longer serves a
            # purpose -- it exists solely to stop a known, concurrently
            # playing chime from being mistaken for speech *onset*. Without
            # this guard, live chunks arriving during what would still be the
            # chime's window would fall into lead_in_buffer, which is only
            # ever folded into `chunks` from the not-yet-started branch below
            # -- with speech already started, that fold-in never runs again,
            # silently dropping those chunks instead of recording them.
            if not speech_started and lead_in_elapsed < lead_in_seconds:
                lead_in_elapsed += chunk_duration
                lead_in_buffer.append(chunk)
                continue

            elapsed += chunk_duration
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            is_loud = rms > SILENCE_RMS_THRESHOLD
            consecutive_loud_chunks = consecutive_loud_chunks + 1 if is_loud else 0

            if speech_started:
                chunks.append(chunk)
                if consecutive_loud_chunks >= min_speech_run_chunks:
                    # A sustained run over threshold -- this is really speech
                    # (the user talking again), not an isolated background
                    # blip. Reset the silence countdown.
                    silence_elapsed = 0.0
                else:
                    # Either true quiet, or a loud blip too brief to count as
                    # renewed speech -- both count toward ending this turn.
                    silence_elapsed += chunk_duration
                if silence_elapsed >= silence_duration:
                    break
            else:
                # Not yet confirmed as real speech -- buffer this run of loud
                # chunks (discarding it if the streak breaks before reaching
                # the threshold, same noise-rejection as before) instead of
                # dropping it outright, so it can be recovered once the run
                # is actually confirmed.
                if is_loud:
                    pending_speech_buffer.append(chunk)
                else:
                    pending_speech_buffer = []

                if consecutive_loud_chunks >= min_onset_chunks:
                    if lead_in_buffer:
                        chunks.extend(lead_in_buffer)
                        lead_in_buffer = []
                    chunks.extend(pending_speech_buffer)
                    pending_speech_buffer = []
                    speech_started = True
                elif elapsed >= initial_timeout:
                    return None
    finally:
        stream.stop()
        stream.close()

    if not chunks:
        return None

    audio = np.concatenate(chunks)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())

    return filepath
