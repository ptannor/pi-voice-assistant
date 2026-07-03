# pi-voice-assistant

Milestone 1 of a Raspberry Pi 4 voice assistant: verify that the microphone
and speaker are actually working before building anything smarter on top.

Milestone 1 covers device discovery, a 6.5-second recording, and playback,
with error handling for the usual Pi audio headaches (missing devices,
permissions, ALSA/PulseAudio/PipeWire confusion, sample rate mismatches).
A minimal wake-word proof-of-concept (`wake_word_daemon.py` — "Alexa" triggers
a canned response, no STT/LLM yet) is also in place; see [Wake
word](#wake-word-alexa) below.

> **What's actually been tested:** the full record → save → playback round
> trip has been verified on the actual target hardware — a **Raspberry Pi 4
> Model B** with a real **HyperX QuadCast S** USB microphone — over Ethernet.
> It was also verified earlier on a development machine during initial
> implementation. Beyond the QuadCast S, the project assumes a **generic USB
> microphone** and a **generic Bluetooth speaker**: nothing here depends on
> QuadCast- or vendor-specific drivers, but no other specific mic/speaker
> model has been tried, and Bluetooth speaker output hasn't been verified
> end-to-end yet (see [Bluetooth speaker
> setup](#bluetooth-speaker-setup)). The [wake word](#wake-word-alexa)
> pipeline is verified up to the mic-capture stage (confirmed real audio
> flows through at the correct format via direct RMS measurement) but actual
> "Alexa" **detection with a real human voice is not yet confirmed** — a
> synthetic TTS test voice didn't trigger it, which may just mean the model
> needs real speech rather than indicating a bug.

## Hardware

- Raspberry Pi 4 Model B
- A USB microphone — defaults to matching **HyperX QuadCast S** by name
  (`audio_check/config.py` → `input_name_hint`), but any generic USB
  microphone works; change the hint or pass `--device-hint` for a different one
- A speaker — wired (3.5mm/USB/HDMI) or a generic Bluetooth speaker. Bluetooth
  speakers need pairing first — see [Bluetooth speaker
  setup](#bluetooth-speaker-setup)

## Project layout

```
pi-voice-assistant/
├── main.py              # entry point
├── wake_word_daemon.py  # always-on: "Alexa" -> canned "hey" response (see below)
├── audio_check/
│   ├── config.py        # sample rate, channels, duration, device name hints
│   ├── devices.py       # enumerate & select input/output devices
│   ├── recorder.py      # record -> WAV
│   ├── player.py        # WAV -> playback
│   ├── errors.py        # friendly exception types
│   └── cli.py           # CLI commands + interactive menu
├── assets/
│   └── hey.wav          # canned wake-word response clip
├── systemd/
│   └── pi-voice-assistant.service   # unit file for wake_word_daemon.py
├── docs/specs/          # design specs written before implementing risky features
├── recordings/          # test WAV output (gitignored)
├── pyproject.toml       # dependencies (numpy, sounddevice, openwakeword)
└── uv.lock
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

5. **Python environment** — no manual venv creation or activation needed;
   `uv sync` reads `pyproject.toml` and handles it:

   ```bash
   git clone https://github.com/ptannor/pi-voice-assistant.git
   cd pi-voice-assistant
   uv sync
   ```

## Usage

```bash
uv run main.py list-devices     # show all input/output devices, with defaults marked
uv run main.py record           # record 6.5s from the mic (auto-picks "QuadCast" by name)
uv run main.py playback         # play back the last recording
uv run main.py test             # full round trip: record then play back
uv run main.py                  # no args -> interactive menu with the same 4 options
```

Options:

```bash
uv run main.py record --seconds 10 --file recordings/longer.wav
uv run main.py playback --file recordings/longer.wav
uv run main.py record --device-hint "USB"   # override the default device match
```

By default the mic is selected by matching `"QuadCast"` in the device name
(see `audio_check/config.py`); the speaker falls back to the system default
output. Change `input_name_hint` / `output_name_hint` in `config.py` if your
setup differs.

## Updating the Pi

Once SSH access is set up (a dedicated key, e.g. `~/.ssh/pi_voice_assistant`,
copied to the Pi with `ssh-copy-id`), pull the latest code onto the Pi from a
machine that has that access, without needing to log into the Pi manually:

```bash
./update-pi.sh
```

This SSHes in, does a fast-forward `git pull`, and runs `uv sync` to bring
dependencies up to date — this creates the venv on first run too, so an
initial `uv sync` on the Pi isn't strictly required beforehand, but cloning
and syncing once yourself (per Setup above) is still the clearer first step.

The first time you run it, it'll ask for your Pi's SSH username (since that's
personal to your setup, not something to hardcode in a shared script) and
offer to save it to `.pi-config` — a local, gitignored file — so you're not
asked again on future runs.

Override any default with environment variables if your setup differs:

```bash
PI_USER=philip PI_HOST=raspberrypi.local PI_DIR=pi-voice-assistant \
  SSH_KEY=~/.ssh/pi_voice_assistant ./update-pi.sh
```

## Bluetooth speaker setup

Only needed if you're using a Bluetooth speaker instead of a wired one —
skip to [Verifying](#verifying-the-microphone--speaker-on-the-pi) otherwise.

Bluetooth speakers don't show up in `aplay -l` — that command only lists
ALSA **hardware** cards (built-in jack, HDMI, USB). A Bluetooth speaker is a
*virtual* sink created by the sound server (PipeWire on Bookworm, PulseAudio
on older images), so it has to be paired and connected before the OS or this
project can see it at all.

1. **Pair and connect** (works the same for any generic Bluetooth speaker;
   replace the MAC address with whatever `scan on` shows for yours):

   ```bash
   bluetoothctl
   > scan on
   > pair XX:XX:XX:XX:XX:XX
   > trust XX:XX:XX:XX:XX:XX
   > connect XX:XX:XX:XX:XX:XX
   > exit
   ```

2. **Confirm the sound server sees it** — check whichever one your image
   runs (`pactl info` in Troubleshooting below tells you which):

   ```bash
   wpctl status              # Bookworm / PipeWire — look under "Sinks"
   pactl list sinks short    # Older images / PulseAudio
   ```

3. **Set it as the default output** so both the OS and
   `uv run main.py list-devices`/`test` pick it up automatically:

   ```bash
   wpctl set-default <sink-id>        # PipeWire
   pactl set-default-sink <name>      # PulseAudio
   ```

   Or via `raspi-config` → System Options → Audio.

4. Re-run `uv run main.py list-devices` — the speaker should now appear
   with output channels > 0. If it doesn't, the sound server isn't routing
   to it yet; recheck step 3 before assuming this project's code is at fault.

## Verifying the microphone + speaker on the Pi

Exact commands to run, in order:

```bash
# 1. OS-level sanity check (wired speakers only — Bluetooth speakers won't
#    appear here even once fully working; see Bluetooth speaker setup above)
arecord -l
aplay -l

# 2. Confirm PortAudio/Python sees the same devices
uv run main.py list-devices

# 3. Full round trip — speak into the mic when it says "Recording..."
uv run main.py test
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
not in `uv run main.py list-devices`, or vice versa. Check which sound
server is actually running:

```bash
pactl info          # PulseAudio/PipeWire — shows the active server + default sink/source
systemctl --user status pipewire pipewire-pulse   # Bookworm
```

If `list-devices` and `arecord -l` disagree, restart the sound server:

```bash
systemctl --user restart pipewire pipewire-pulse   # or: pulseaudio -k (older images)
```

Using a Bluetooth speaker? It's normal for it to be invisible to
`arecord -l`/`aplay -l` — see [Bluetooth speaker
setup](#bluetooth-speaker-setup).

**Wrong sample rate / recording fails immediately**
USB microphones advertise specific supported rates (the QuadCast S, for
example, is typically 48000 Hz). If the configured `sample_rate` in
`config.py` isn't supported by the device,
`recorder.py` automatically retries once at the device's own default rate and
prints a warning — no action needed unless both attempts fail, in which case
the error message includes the device name so you can check its supported
rates against `list-devices`.

**Wrong device selected (e.g. HDMI instead of the speaker you expected)**
Use `uv run main.py list-devices` to find the correct index, then pass it
explicitly:

```bash
uv run main.py playback --device-hint "USB"
```

Or set the Pi's default output device via `raspi-config` → System Options →
Audio.

**`update-pi.sh` fails with `cd: pi-voice-assistant: No such file or directory`**
The script assumes the repo was cloned directly into your home directory on
the Pi (`~/pi-voice-assistant`). If you cloned it somewhere else (e.g.
`~/Code/pi-voice-assistant`), set `PI_DIR` to that path relative to home in
your `.pi-config` — e.g. `PI_DIR=Code/pi-voice-assistant`.

**`update-pi.sh` fails with `uv: command not found`**
`uv`'s installer adds `~/.local/bin` to `PATH` via your shell's rc file
(`.bashrc`/`.profile`), but `ssh host command` runs a non-interactive,
non-login shell that doesn't source those files — so `uv` is installed and
works fine when you SSH in manually, but isn't found when driven remotely.
`update-pi.sh` already works around this by exporting
`PATH="$HOME/.local/bin:$PATH"` before running `uv`; if you hit this outside
the script (e.g. in your own automation), add the same line.

**`update-pi.sh` (or `uv sync` on the Pi) fails with a 401 fetching a package from an internal registry**
This means `uv.lock` was regenerated on a machine with a corporate/internal
package registry proxy set as the default package index (e.g. via a
`UV_INDEX` environment variable and/or a global `uv` config file) — the Pi
has no access to that proxy and never should. This repo's `pyproject.toml`
pins a `[[tool.uv.index]]` override to public PyPI specifically for this
project, but **environment variables take precedence over it**, so
regenerating the lockfile on such a machine without unsetting the override
first will silently re-poison `uv.lock` with internal-registry URLs again.
If you ever update dependencies here on a machine with a corporate registry
default, unset any `UV_INDEX*` environment variables first, e.g.:
```bash
env -u UV_INDEX uv sync
```
(add more `-u` flags for any other `UV_INDEX_*` variables your setup sets),
then confirm the lockfile only references `pypi.org` before committing.

## Wake word ("Alexa")

`wake_word_daemon.py` listens continuously for the wake word **"Alexa"** and
plays a canned "hey" response when it hears it — no speech-to-text, no LLM,
just proving the always-on detect-and-respond loop works. This uses
[openWakeWord](https://github.com/dscripka/openWakeWord)'s free, fully
open-source pretrained "alexa" model — **no account, no API key, no signup**
required at all, unlike Porcupine (which was the original choice for this
proof-of-concept using its "jarvis" keyword, until Picovoice discontinued
its free tier in June 2026 and replaced it with a 7-day trial). "Alexa" is a
placeholder for the real custom-trained wake words ("Menachem Mendel" /
"Mendy") planned for a later milestone, also via openWakeWord.

**Run it** (no setup beyond `uv sync` — model files download automatically
on first run):

```bash
uv run wake_word_daemon.py
```

Say "Alexa" — you should hear the canned response play back.

**Verification status:** the mic-capture and playback pipeline is confirmed
working (real audio flows through at the correct 16kHz format, response WAV
plays correctly), but actual wake-word *detection* with a real human voice
has not yet been confirmed — testing with macOS's synthetic TTS voice didn't
trigger it, which may simply mean the model needs real speech rather than
indicating a bug. If it doesn't trigger for you, check: the mic's physical
gain isn't turned down (a real issue hit earlier in this project), you're
speaking at a normal distance/volume, and the terminal actually printed
`Listening for 'alexa' on '...'...` before you spoke.

## Running as a background service

`systemd/pi-voice-assistant.service` is a template unit file that runs
`wake_word_daemon.py` persistently — survives SSH disconnects, restarts on
crash, starts on boot. Install it on the Pi:

```bash
sudo cp systemd/pi-voice-assistant.service /etc/systemd/system/
sudo nano /etc/systemd/system/pi-voice-assistant.service   # replace <PI_USER> and <PI_DIR>
sudo systemctl daemon-reload
sudo systemctl enable --now pi-voice-assistant
```

Note `ExecStart` uses the **absolute path** to `uv`
(`/home/<PI_USER>/.local/bin/uv`) rather than relying on `PATH` — systemd
services start with an even more minimal environment than a non-interactive
SSH session, so the same PATH issue described above applies here too, just
with no shell to add a workaround line to. No secrets/`.env` needed for this
service — openWakeWord requires no account or API key at all.

Check on it:

```bash
systemctl status pi-voice-assistant
journalctl -u pi-voice-assistant -f   # watch for "Wake word detected: alexa"
```

Stop it (e.g. before running `uv run main.py test` manually, so both aren't
fighting over the mic at once):

```bash
sudo systemctl stop pi-voice-assistant
```

## Roadmap (not in this milestone)

- Custom-trained wake words ("Menachem Mendel" / "Mendy" via openWakeWord) —
  `wake_word_daemon.py`'s "Alexa" is a pretrained placeholder, not the real thing
- Hebrew + English speech recognition
- Timers
- Spotify control
- Zmanim lookups
- Shabbat mode — see `docs/specs/shabbat-gating.md` for the (unimplemented) design
