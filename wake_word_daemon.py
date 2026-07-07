#!/usr/bin/env python3
"""Wake word daemon: listens for "Alexa", then records a question, sends it
through Claude, and speaks the reply back -- in whichever of English/Hebrew
the user actually spoke.

Uses openWakeWord's free, fully open-source pretrained "alexa" model (no
account, no API key, no signup) as a stand-in for the eventual custom-trained
"Menachem Mendel" / "Mendy" wake words.
"""
from __future__ import annotations

import queue
import sys
import tempfile
import time
from pathlib import Path

import openwakeword
import sounddevice as sd
from openwakeword.model import Model

from audio_check.config import DEFAULT_CONFIG
from audio_check.devices import Device, find_input_device, find_output_device
from audio_check.errors import AudioCheckError, PlaybackFailed, RecordingFailed
from audio_check.player import play_wav
from audio_check.recorder import record_to_wav
from brain.llm import BrainError, ask
from brain.respond import synthesize_reply
from brain.stt import TranscriptionError, transcribe

ACK_WAV = Path(__file__).parent / "assets" / "hey.wav"
WAKE_WORD = "alexa"
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80ms at 16kHz -- openWakeWord's recommended chunk size
DETECTION_THRESHOLD = 0.5
COOLDOWN_SECONDS = 2.0  # ignore re-triggers right as we resume listening
QUERY_SECONDS = 6.0  # fixed-duration recording of the user's question after the wake word


def _listen_for_wake_word(model: Model, in_device: Device, last_trigger: float) -> float:
    """Block until the wake word is detected; return the new last_trigger time.

    Runs the InputStream inside this function's `with` block so it's fully
    closed before we record the user's question -- avoids a second
    concurrent stream fighting the wake-word one for the same device.
    """
    audio_queue: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        # Keep this callback as fast as possible -- it runs on a real-time audio
        # thread that must keep draining the hardware buffer. Model inference is
        # too slow to run here reliably on a Pi 4's CPU (was causing intermittent
        # "input overflow" and missed detections); just hand the chunk off.
        if status:
            print(f"Stream status: {status}", file=sys.stderr, flush=True)
        audio_queue.put(indata[:, 0].copy())

    with sd.InputStream(
        device=in_device.index,
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
        callback=callback,
    ):
        while True:
            pcm = audio_queue.get()
            prediction = model.predict(pcm)
            score = prediction.get(WAKE_WORD, 0.0)
            now = time.monotonic()
            if score > DETECTION_THRESHOLD and (now - last_trigger) > COOLDOWN_SECONDS:
                print(f"Wake word detected: {WAKE_WORD} (score={score:.2f})", flush=True)
                return now


def _handle_conversation(in_device: Device, out_device: Device) -> None:
    query_wav = Path(tempfile.mktemp(suffix=".wav"))
    reply_wav: Path | None = None
    try:
        play_wav(ACK_WAV, out_device)  # quick chime so the user knows to start talking
        record_to_wav(in_device, query_wav, QUERY_SECONDS, SAMPLE_RATE, 1)
        text, language = transcribe(query_wav)
        print(f"Heard ({language}): {text}", flush=True)
        if not text:
            return

        reply = ask(text, language)
        print(f"Claude: {reply}", flush=True)

        reply_wav = synthesize_reply(reply)
        play_wav(reply_wav, out_device)
    except (TranscriptionError, BrainError, RecordingFailed, PlaybackFailed) as exc:
        print(f"Conversation turn failed: {exc}", file=sys.stderr, flush=True)
    except Exception as exc:
        print(f"Unexpected error handling conversation: {exc!r}", file=sys.stderr, flush=True)
    finally:
        query_wav.unlink(missing_ok=True)
        if reply_wav is not None:
            reply_wav.unlink(missing_ok=True)


def main() -> None:
    cfg = DEFAULT_CONFIG
    try:
        in_device = find_input_device(cfg.input_name_hint)
        out_device = find_output_device(cfg.output_name_hint)
    except AudioCheckError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    openwakeword.utils.download_models(model_names=[WAKE_WORD])
    # Force onnx: the tflite_runtime wheel available on some platforms (e.g. the
    # Pi's aarch64 build) is compiled against NumPy 1.x and breaks under NumPy 2.x
    # ("_ARRAY_API not found"). onnxruntime works correctly on both dev machine and Pi.
    model = Model(wakeword_models=[WAKE_WORD], inference_framework="onnx")

    print(
        f"Listening for '{WAKE_WORD}' on '{in_device.name}' (index {in_device.index})...",
        flush=True,
    )
    print(f"Responses play on '{out_device.name}' (index {out_device.index})", flush=True)

    last_trigger = 0.0
    while True:
        last_trigger = _listen_for_wake_word(model, in_device, last_trigger)
        _handle_conversation(in_device, out_device)
        last_trigger = time.monotonic()  # restart cooldown from when we resume listening


if __name__ == "__main__":
    main()
