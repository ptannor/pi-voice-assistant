from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG, AudioConfig
from .errors import AudioCheckError
from .devices import find_input_device, find_output_device, list_devices, print_devices
from .player import play_wav
from .recorder import record_to_wav


def cmd_list_devices(_args: argparse.Namespace, _cfg: AudioConfig) -> None:
    print_devices(list_devices())


def cmd_record(args: argparse.Namespace, cfg: AudioConfig) -> None:
    device = find_input_device(args.device_hint if args.device_hint else cfg.input_name_hint)
    print(f"Recording {args.seconds}s from '{device.name}' (index {device.index})...")
    path = record_to_wav(
        device=device,
        filepath=Path(args.file),
        duration_seconds=args.seconds,
        sample_rate=cfg.sample_rate,
        channels=cfg.channels,
    )
    print(f"Saved recording to {path}")


def cmd_playback(args: argparse.Namespace, cfg: AudioConfig) -> None:
    device = find_output_device(args.device_hint if args.device_hint else cfg.output_name_hint)
    print(f"Playing {args.file} through '{device.name}' (index {device.index})...")
    play_wav(Path(args.file), device)
    print("Playback finished.")


def cmd_test(args: argparse.Namespace, cfg: AudioConfig) -> None:
    in_device = find_input_device(cfg.input_name_hint)
    out_device = find_output_device(cfg.output_name_hint)
    print(f"Microphone: {in_device.name} (index {in_device.index})")
    print(f"Speaker:    {out_device.name} (index {out_device.index})")

    print(f"\nRecording {cfg.duration_seconds}s — speak now...")
    path = record_to_wav(
        device=in_device,
        filepath=cfg.test_filepath,
        duration_seconds=cfg.duration_seconds,
        sample_rate=cfg.sample_rate,
        channels=cfg.channels,
    )
    print(f"Saved to {path}")

    print("\nPlaying it back...")
    play_wav(path, out_device)
    print("\nDone. If you heard your recording, mic + speaker are both working.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi-voice-assistant",
        description="Verify microphone and speaker connectivity on a Raspberry Pi.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list-devices", help="List available audio input/output devices")

    record_p = sub.add_parser("record", help="Record test audio from the microphone")
    record_p.add_argument("--seconds", type=float, default=DEFAULT_CONFIG.duration_seconds)
    record_p.add_argument("--file", default=str(DEFAULT_CONFIG.test_filepath))
    record_p.add_argument("--device-hint", default=None, help="Substring to match device name")

    playback_p = sub.add_parser("playback", help="Play back a recorded WAV file")
    playback_p.add_argument("--file", default=str(DEFAULT_CONFIG.test_filepath))
    playback_p.add_argument("--device-hint", default=None, help="Substring to match device name")

    sub.add_parser("test", help="Run the full record + playback round trip")

    return parser


_MENU = {
    "1": ("list-devices", cmd_list_devices),
    "2": ("record", cmd_record),
    "3": ("playback", cmd_playback),
    "4": ("test", cmd_test),
}


def _run_interactive(cfg: AudioConfig) -> None:
    parser = _build_parser()
    print("pi-voice-assistant — audio hardware check")
    print("  1) list devices")
    print("  2) record test audio")
    print("  3) playback test audio")
    print("  4) run full mic + speaker test")
    choice = input("Choose 1-4: ").strip()

    entry = _MENU.get(choice)
    if entry is None:
        print("Invalid choice.")
        sys.exit(1)

    command, handler = entry
    args = parser.parse_args([command])
    handler(args, cfg)


def main(argv: list[str] | None = None, cfg: AudioConfig = DEFAULT_CONFIG) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "list-devices": cmd_list_devices,
        "record": cmd_record,
        "playback": cmd_playback,
        "test": cmd_test,
    }

    try:
        if args.command is None:
            _run_interactive(cfg)
        else:
            handlers[args.command](args, cfg)
    except AudioCheckError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
