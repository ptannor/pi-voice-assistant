"""CLI entry point: uv run python -m wakeword_bench.cli --model /path/to/mendy.onnx"""
from __future__ import annotations

import argparse
from pathlib import Path

from .bench import print_report, run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Record real test conditions (distance, background noise) and "
            "compare a custom wake-word model against openWakeWord's "
            "pretrained 'alexa' reference model."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Path to your trained .onnx wake-word model (e.g. mendy.onnx). "
             "Omit to only benchmark the alexa reference model.",
    )
    parser.add_argument(
        "--device-hint",
        default=None,
        help="Substring to match the input device name (e.g. 'reSpeaker'). "
             "Omit to use the system default input.",
    )
    parser.add_argument("--seconds", type=float, default=5.0, help="Recording duration per condition")
    parser.add_argument(
        "--output-dir",
        default="recordings/wakeword_bench",
        help="Where to save the recorded clips",
    )
    args = parser.parse_args(argv)

    results = run(
        mendy_model_path=args.model,
        device_hint=args.device_hint,
        seconds=args.seconds,
        output_dir=Path(args.output_dir),
    )
    print_report(results)


if __name__ == "__main__":
    main()
