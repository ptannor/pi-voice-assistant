# pi-voice-assistant

Milestone 1 of a Raspberry Pi 4 voice assistant: verify that the microphone
and speaker are actually working before building anything smarter on top.

No wake word, no speech recognition yet — just device discovery, a 5-second
recording, and playback, with error handling for the usual Pi audio headaches
(missing devices, permissions, ALSA/PulseAudio/PipeWire confusion, sample
rate mismatches).

## Hardware

- Raspberry Pi 4 Model B
- HyperX QuadCast S USB microphone
- Any speaker or audio output (3.5mm, USB, HDMI, or Bluetooth)

## Project layout

```
pi-voice-assistant/
├── main.py              # entry point
├── audio_check/
│   ├── config.py        # sample rate, channels, duration, device name hints
│   ├── devices.py       # enumerate & select input/output devices
│   ├── recorder.py      # record -> WAV
│   ├── player.py        # WAV -> playback
│   ├── errors.py        # friendly exception types
│   └── cli.py           # CLI commands + interactive menu
├── recordings/          # test WAV output (gitignored)
└── requirements.txt
```

Kept deliberately small and modular so later milestones (wake word detection,
Hebrew/English speech recognition, timers, Spotify, zmanim, Shabbat mode) can
each become their own module without touching this one.

## Setup on Raspberry Pi OS

1. **System packages** — PortAudio needs ALSA underneath it:

   ```bash
   sudo apt update
   sudo apt install -y libportaudio2 libasound2-dev alsa-utils
   ```

2. **Audio group permission** — avoids permission-denied errors opening the
   mic/speaker:

   ```bash
   sudo usermod -aG audio $USER
   ```

   Log out and back in (or reboot) for this to take effect.

3. **Confirm the OS sees the hardware** before touching Python at all:

   ```bash
   arecord -l   # should list "HyperX QuadCast S" as a capture device
   aplay -l     # should list your speaker as a playback device
   ```

   If either command shows nothing, this is a hardware/OS problem, not a
   Python problem — check the USB connection and `dmesg | tail` for the mic,
   and check `raspi-config` → System Options → Audio for the speaker output.

4. **Install `uv`** (one-time, not preinstalled on Raspberry Pi OS):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   source $HOME/.local/bin/env
   ```

5. **Python environment**:

   ```bash
   git clone https://github.com/ptannor/pi-voice-assistant.git
   cd pi-voice-assistant
   uv venv .venv
   source .venv/bin/activate
   uv pip install -r requirements.txt
   ```

## Usage

```bash
python3 main.py list-devices     # show all input/output devices, with defaults marked
python3 main.py record           # record 5s from the mic (auto-picks "QuadCast" by name)
python3 main.py playback         # play back the last recording
python3 main.py test             # full round trip: record then play back
python3 main.py                  # no args -> interactive menu with the same 4 options
```

Options:

```bash
python3 main.py record --seconds 10 --file recordings/longer.wav
python3 main.py playback --file recordings/longer.wav
python3 main.py record --device-hint "USB"   # override the default device match
```

By default the mic is selected by matching `"QuadCast"` in the device name
(see `audio_check/config.py`); the speaker falls back to the system default
output. Change `input_name_hint` / `output_name_hint` in `config.py` if your
setup differs.

## Verifying the QuadCast S + speaker on the Pi

Exact commands to run, in order:

```bash
# 1. OS-level sanity check
arecord -l
aplay -l

# 2. Confirm PortAudio/Python sees the same devices
python3 main.py list-devices

# 3. Full round trip — speak into the QuadCast S when it says "Recording..."
python3 main.py test
```

If you hear your own voice played back through the speaker, both the
microphone and speaker are confirmed working end to end.

## Troubleshooting

**"No input (microphone) devices detected at all"**
Run `arecord -l`. If it's empty too, it's a USB/hardware issue — try a
different USB port (prefer USB 2.0 ports on the Pi 4, not the USB 3.0 blue
ones, if you see intermittent dropouts), or `dmesg | tail` after plugging it
in to see if the kernel registered it at all.

**Permission denied opening the microphone/speaker**
You're not in the `audio` group yet, or haven't re-logged in since being
added: `sudo usermod -aG audio $USER`, then log out and back in.

**ALSA / PulseAudio / PipeWire fighting each other**
Raspberry Pi OS Bookworm ships PipeWire by default; older Bullseye images may
have bare ALSA or PulseAudio. Symptoms: device shows up in `arecord -l` but
not in `python3 main.py list-devices`, or vice versa. Check which sound
server is actually running:

```bash
pactl info          # PulseAudio/PipeWire — shows the active server + default sink/source
systemctl --user status pipewire pipewire-pulse   # Bookworm
```

If `list-devices` and `arecord -l` disagree, restart the sound server:

```bash
systemctl --user restart pipewire pipewire-pulse   # or: pulseaudio -k (older images)
```

**Wrong sample rate / recording fails immediately**
The QuadCast S advertises specific supported rates (typically 48000 Hz). If
the configured `sample_rate` in `config.py` isn't supported by the device,
`recorder.py` automatically retries once at the device's own default rate and
prints a warning — no action needed unless both attempts fail, in which case
the error message includes the device name so you can check its supported
rates against `list-devices`.

**Wrong device selected (e.g. HDMI instead of the speaker you expected)**
Use `python3 main.py list-devices` to find the correct index, then pass it
explicitly:

```bash
python3 main.py playback --device-hint "USB"
```

Or set the Pi's default output device via `raspi-config` → System Options →
Audio.

## Roadmap (not in this milestone)

- Wake word detection
- Hebrew + English speech recognition
- Timers
- Spotify control
- Zmanim lookups
- Shabbat mode
