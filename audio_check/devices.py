"""Device discovery and selection.

All PortAudio/ALSA access is funneled through this module so the rest of the
codebase never touches `sounddevice` directly and error handling stays in one
place.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    import sounddevice as sd
except OSError as exc:
    # PortAudio couldn't find a usable host API (common on a fresh Pi image
    # before libportaudio2/ALSA is set up correctly).
    raise ImportError(
        "sounddevice could not load the PortAudio backend. On Raspberry Pi OS "
        "run: sudo apt install -y libasound2-dev libportaudio2 alsa-utils"
    ) from exc

from .errors import AudioBackendError, NoInputDeviceFound, NoOutputDeviceFound


@dataclass
class Device:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0


def list_devices() -> list[Device]:
    try:
        raw = sd.query_devices()
    except sd.PortAudioError as exc:
        raise AudioBackendError(
            "PortAudio failed to query devices. This usually means ALSA, "
            "PulseAudio, and PipeWire are fighting over the sound card. Try: "
            "'aplay -l' and 'arecord -l' to see what the OS itself detects, "
            "then re-run this tool."
        ) from exc

    return [
        Device(
            index=i,
            name=d["name"],
            max_input_channels=d["max_input_channels"],
            max_output_channels=d["max_output_channels"],
            default_samplerate=d["default_samplerate"],
        )
        for i, d in enumerate(raw)
    ]


def print_devices(devices: list[Device] | None = None) -> None:
    devices = devices if devices is not None else list_devices()
    default_in, default_out = _default_indices()

    print(f"{'Idx':>3}  {'In':>3}  {'Out':>3}  {'Rate':>7}  Name")
    print("-" * 60)
    for d in devices:
        markers = []
        if d.index == default_in:
            markers.append("default in")
        if d.index == default_out:
            markers.append("default out")
        suffix = f"  <- {', '.join(markers)}" if markers else ""
        print(
            f"{d.index:>3}  {d.max_input_channels:>3}  {d.max_output_channels:>3}  "
            f"{int(d.default_samplerate):>7}  {d.name}{suffix}"
        )


def _default_indices() -> tuple[int | None, int | None]:
    try:
        default_in, default_out = sd.default.device
    except Exception:
        return None, None
    return default_in, default_out


def find_input_device(name_hint: str | None) -> Device:
    devices = [d for d in list_devices() if d.is_input]
    if not devices:
        raise NoInputDeviceFound(
            "No input (microphone) devices detected at all. Check the USB "
            "connection and run 'arecord -l' on the Pi to confirm the OS sees it."
        )

    if name_hint:
        matches = [d for d in devices if name_hint.lower() in d.name.lower()]
        if matches:
            return matches[0]
        raise NoInputDeviceFound(
            f"No input device matching '{name_hint}' found. Available input "
            f"devices: {', '.join(d.name for d in devices)}"
        )

    default_in, _ = _default_indices()
    for d in devices:
        if d.index == default_in:
            return d
    return devices[0]


def find_output_device(name_hint: str | None) -> Device:
    devices = [d for d in list_devices() if d.is_output]
    if not devices:
        raise NoOutputDeviceFound(
            "No output (speaker) devices detected at all. Check the audio "
            "cable/HDMI connection and run 'aplay -l' on the Pi to confirm "
            "the OS sees it."
        )

    if name_hint:
        matches = [d for d in devices if name_hint.lower() in d.name.lower()]
        if matches:
            return matches[0]
        raise NoOutputDeviceFound(
            f"No output device matching '{name_hint}' found. Available output "
            f"devices: {', '.join(d.name for d in devices)}"
        )

    _, default_out = _default_indices()
    for d in devices:
        if d.index == default_out:
            return d
    return devices[0]
