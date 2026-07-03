#!/usr/bin/env python3
"""Wake word daemon: listens for "Alexa" and plays a canned response.

No STT, no LLM — proves the always-on wake-word detection pipeline works
before building the real response pipeline on top of it. Uses openWakeWord's
free, fully open-source pretrained "alexa" model (no account, no API key, no
signup) as a stand-in for the eventual custom-trained "Menachem Mendel" /
"Mendy" wake words.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import openwakeword
import sounddevice as sd
from openwakeword.model import Model

from audio_check.config import DEFAULT_CONFIG
from audio_check.devices import find_input_device, find_output_device
from audio_check.errors import AudioCheckError
from audio_check.player import play_wav

RESPONSE_WAV = Path(__file__).parent / "assets" / "hey.wav"
WAKE_WORD = "alexa"
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80ms at 16kHz -- openWakeWord's recommended chunk size
DETECTION_THRESHOLD = 0.5
COOLDOWN_SECONDS = 2.0  # ignore re-triggers while the response is still playing/echoing


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
    print(f"Response plays on '{out_device.name}' (index {out_device.index})", flush=True)

    last_trigger = 0.0

    def callback(indata, frames, time_info, status):
        nonlocal last_trigger
        if status:
            print(f"Stream status: {status}", file=sys.stderr, flush=True)
        pcm = indata[:, 0]
        prediction = model.predict(pcm)
        score = prediction.get(WAKE_WORD, 0.0)
        now = time.monotonic()
        if score > DETECTION_THRESHOLD and (now - last_trigger) > COOLDOWN_SECONDS:
            last_trigger = now
            print(f"Wake word detected: {WAKE_WORD} (score={score:.2f})", flush=True)
            # Blocks this callback thread until playback finishes -- acceptable for
            # this proof-of-concept since we don't want to detect our own response anyway.
            play_wav(RESPONSE_WAV, out_device)

    with sd.InputStream(
        device=in_device.index,
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
        callback=callback,
    ):
        while True:
            sd.sleep(1000)


if __name__ == "__main__":
    main()
