"""Compare a custom wake-word model against openWakeWord's pretrained
"alexa" model -- the same one wake_word_daemon.py falls back to before a
custom model is trained (see WAKE_WORD in wake_word_daemon.py) -- across a
battery of real recorded conditions (distance, background noise).
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import openwakeword.utils
from openwakeword.model import Model

from audio_check.devices import find_input_device
from audio_check.recorder import record_to_wav

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80ms at 16kHz -- matches wake_word_daemon.py's CHUNK_SAMPLES
ALEXA_THRESHOLD = 0.6  # wake_word_daemon.py's DETECTION_THRESHOLD for "alexa"

CONDITIONS = [
    "close (~0.5m), quiet room",
    "medium distance (~2m), quiet room",
    "far (~4m+), quiet room",
    "close (~0.5m), with background music playing",
    "far (~4m+), with background music playing",
]


@dataclass
class ScoredModel:
    label: str
    key: str
    model: Model


def load_models(mendy_model_path: str | Path | None) -> list[ScoredModel]:
    models: list[ScoredModel] = []
    if mendy_model_path:
        mendy_model_path = str(mendy_model_path)
        models.append(ScoredModel(
            label="mendy",
            key=Path(mendy_model_path).stem,
            model=Model(wakeword_models=[mendy_model_path], inference_framework="onnx"),
        ))

    # openWakeWord's own pretrained model -- the real Amazon Alexa engine is
    # proprietary/cloud-only and not available to test against locally; this
    # is the closest fair, same-methodology reference actually obtainable.
    openwakeword.utils.download_models(model_names=["alexa"])
    models.append(ScoredModel(
        label="alexa (reference)",
        key="alexa",
        model=Model(wakeword_models=["alexa"], inference_framework="onnx"),
    ))
    return models


def _read_wav_int16(filepath: Path) -> np.ndarray:
    with wave.open(str(filepath), "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE, f"{filepath} must be {SAMPLE_RATE}Hz"
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16)


def score_clip(scored: ScoredModel, audio: np.ndarray) -> float:
    scored.model.reset()
    scores = [
        scored.model.predict(audio[i:i + CHUNK_SAMPLES])[scored.key]
        for i in range(0, len(audio) - CHUNK_SAMPLES, CHUNK_SAMPLES)
    ]
    return float(max(scores)) if scores else 0.0


def run(
    mendy_model_path: str | Path | None,
    device_hint: str | None,
    seconds: float,
    output_dir: Path,
) -> list[tuple[str, dict[str, float]]]:
    models = load_models(mendy_model_path)
    device = find_input_device(device_hint)
    print(f"Using input device: {device.name} (index {device.index})\n")

    results: list[tuple[str, dict[str, float]]] = []
    for i, condition in enumerate(CONDITIONS, 1):
        input(f"[{i}/{len(CONDITIONS)}] {condition}. Get in position, then press Enter to start recording...")
        filepath = output_dir / f"condition_{i}.wav"
        print(f"Recording {seconds}s -- say 'Mendy' now!")
        record_to_wav(
            device=device,
            filepath=filepath,
            duration_seconds=seconds,
            sample_rate=SAMPLE_RATE,
            channels=1,
        )
        audio = _read_wav_int16(filepath)
        scores = {m.label: score_clip(m, audio) for m in models}
        results.append((condition, scores))
        print("  " + " | ".join(f"{label}: {score:.3f}" for label, score in scores.items()) + "\n")

    return results


def print_report(results: list[tuple[str, dict[str, float]]]) -> None:
    if not results:
        return
    labels = list(results[0][1].keys())
    col_width = max(len(l) for l in labels) + 2

    print(f"{'Condition':45s} | " + " | ".join(f"{l:{col_width}s}" for l in labels))
    print("-" * (45 + 3 + (col_width + 3) * len(labels)))
    for condition, scores in results:
        print(f"{condition:45s} | " + " | ".join(f"{scores[l]:{col_width}.3f}" for l in labels))

    print()
    for label in labels:
        avg = sum(s[label] for _, s in results) / len(results)
        best = max(s[label] for _, s in results)
        print(f"{label}: avg max-score {avg:.3f}, best condition {best:.3f}")
    print(f"\n(For reference: wake_word_daemon.py's real detection threshold for 'alexa' is {ALEXA_THRESHOLD})")
