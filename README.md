# pi-voice-assistant

A Raspberry Pi 4 voice assistant. `wake_word_daemon.py` does real free-form
conversation: "Alexa" triggers a recording of your question, which goes
through Claude and comes back as spoken audio — in whichever of English/Hebrew
you actually spoke. See [Wake word](#wake-word-alexa) below.

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
├── wake_word_daemon.py  # always-on: "Alexa" -> record question -> Claude -> spoken reply
├── audio_check/
│   ├── config.py        # sample rate, channels, duration, device name hints
│   ├── devices.py       # enumerate & select input/output devices
│   ├── recorder.py      # record -> WAV
│   ├── player.py        # WAV -> playback
│   ├── errors.py        # friendly exception types
│   └── cli.py           # CLI commands + interactive menu
├── brain/
│   ├── config.py        # loads ANTHROPIC_API_KEY / GROQ_API_KEY / SERPER_API_KEY from .env
│   ├── stt.py           # speech-to-text via Groq's hosted Whisper (auto EN/HE detection)
│   ├── llm.py           # conversational reply via Anthropic's Claude API, tool-calling loop
│   ├── tools.py         # tool definitions Claude can call (mostly stubs -- see docstring)
│   ├── websearch.py     # real web search via Serper.dev, with a same-day cache
│   ├── memory.py        # long-term household facts/preferences (see Memory section)
│   └── respond.py       # picks Hebrew/English TTS voice from the reply text, synthesizes it
├── hebrew_tts/
│   ├── nakdan.py         # adds nikud via Dicta's free Nakdan API (rare/traditional text only)
│   ├── pronunciation.py  # per-word corrections for Nakdan's mistakes
│   └── synth.py          # Edge TTS synthesis (Hebrew + English voices)
├── shabbat/
│   ├── config.py        # location (from .pi-config), warning offsets, message text
│   ├── hebcal_client.py # fetch + cache candle-lighting/havdalah/Yom Tov data
│   ├── schedule.py      # merge into gate windows, compute scheduled announcements
│   ├── ntp.py           # clock-trustworthiness check (fail closed if unsynced)
│   └── gate.py          # entry point: run every minute via the gate .timer
├── assets/
│   ├── chime.wav        # ascending two-tone chime, played when the wake word triggers
│   └── shabbat/         # pre-recorded Hebrew entrance/exit/warning announcements
├── systemd/
│   ├── pi-voice-assistant.service        # wake_word_daemon.py (user unit)
│   ├── pi-voice-assistant-gate.service   # shabbat/gate.py, one-shot (user unit)
│   └── pi-voice-assistant-gate.timer     # runs the gate checker every minute
├── docs/specs/          # design specs written before implementing risky features
├── recordings/          # test WAV output (gitignored)
├── .env.example         # template for ANTHROPIC_API_KEY / GROQ_API_KEY -- copy to .env
├── pyproject.toml       # dependencies
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

## Wake word ("Alexa") + talking to Claude

`wake_word_daemon.py` listens continuously for the wake word **"Alexa"**.
When it hears it: plays a short acknowledgment chime, records ~6 seconds of
your question, transcribes it (auto-detecting English or Hebrew), sends it to
Claude, and speaks the reply back — in whichever language you spoke. This
uses [openWakeWord](https://github.com/dscripka/openWakeWord)'s free, fully
open-source pretrained "alexa" model for wake-word detection — **no account,
no API key, no signup** required for that part, unlike Porcupine (the
original choice for this proof-of-concept using its "jarvis" keyword, until
Picovoice discontinued its free tier in June 2026 and replaced it with a
7-day trial). "Alexa" is a placeholder for the real custom-trained wake words
("Menachem Mendel" / "Mendy") planned for a later milestone, also via
openWakeWord.

**Training a custom wake word (e.g. "Mendy"):** not something to do locally
in this repo's own dev environment -- openWakeWord's pretrained models are
trained on 30,000+ hours of negative audio (speech/noise/music) to avoid
false triggers, and the training code needs a multi-GB PyTorch + TensorFlow
stack (`pip install openwakeword[full]`) neither of which is practical to
pull into a normal dev machine just to try one word. Do it via openWakeWord's
own free-GPU Colab notebooks instead (linked from
[openWakeWord's README](https://github.com/dscripka/openWakeWord)): the
simple one-click notebook for a quick model, or `automatic_model_training.ipynb`
for a higher-quality one. Either way you'll end up with a `.onnx` file --
drop it anywhere in this repo (it's just a model file, no code changes
needed) and point at it:
```bash
# add to .pi-config:
WAKE_WORD_MODEL_PATH=/path/to/mendy.onnx
```
Leave it unset to keep using the pretrained "alexa" model. Once you have a
real model, expect to tune `DETECTION_THRESHOLD` in `wake_word_daemon.py`
against real speech -- a custom model's confidence distribution won't
necessarily match "alexa"'s.

The conversation itself (`brain/`) does need personal API keys:

1. Copy `.env.example` to `.env` and fill in:
   - `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)
     (Claude generates the reply)
   - `GROQ_API_KEY` — from [console.groq.com](https://console.groq.com)
     (Groq's hosted Whisper does speech-to-text; chosen over OpenAI's own
     Whisper API for cost/speed, and over self-hosting ivrit.ai's
     Hebrew-tuned models — better Hebrew accuracy, but Hebrew-only and
     requires running your own GPU endpoint, not worth the ops overhead here)
   - `SERPER_API_KEY` — from [serper.dev](https://serper.dev) (real web
     search, e.g. "what movies are playing at X" — 2,500 free queries, no
     card required, then pay-as-you-go from $0.001/query; see
     `brain/websearch.py` for why this over Anthropic's native web search
     tool or Brave's API). Optional — only needed for the `web_search` tool;
     everything else works without it.
2. `.env` is gitignored — never commit real keys. **Personal keys only**:
   this is a personal public repo, not Check Point work, so never point
   these at a corporate LLM gateway or credential.
3. (Optional) Add your household's location to `.pi-config` — the same
   gitignored, personal file `SHABBAT_GEONAMEID` lives in — so Claude
   defaults to location-appropriate answers (emergency services, "what's
   nearby", etc.) instead of assuming the US, and knows the actual current
   date/time (an LLM has no built-in clock otherwise):
   ```bash
   # add to .pi-config:
   HOUSEHOLD_LOCATION=Your City, Your Country
   HOUSEHOLD_TIMEZONE=Your/IANA_Timezone   # defaults to Asia/Jerusalem if unset
   ```
   This nudges Claude toward the right country, but it's still an LLM: for
   anything safety-critical (e.g. a specific crisis hotline number), it's
   instructed to say so and point to local emergency services rather than
   guess, instead of confidently stating a number it isn't sure is current
   or correct for your location.

**Run it**:

```bash
uv run wake_word_daemon.py
```

Say "Alexa", wait for the chime, then ask your question.

**Verification status:** the mic-capture, recording, and playback pipeline is
confirmed working, and the STT → Claude → TTS chain (`brain/`) has been
exercised locally with mocked/missing keys to confirm clean error handling —
but the full conversational loop has **not yet been run end-to-end on real
hardware**, since the Pi this was developed on died mid-session (see the
project's own troubleshooting history) and its replacement isn't set up yet.
Once a Pi is available again: confirm wake-word detection still triggers on
real speech (this was previously unconfirmed with synthetic TTS test audio,
which may just mean the model needs real speech), then confirm a full
question → Claude reply → spoken response round trip in both languages.
If wake-word detection doesn't trigger, check: the mic's physical gain isn't
turned down (a real issue hit earlier in this project), you're speaking at a
normal distance/volume, and the terminal printed `Listening for 'alexa' on
'...'...` before you spoke.

## Long-term memory

`brain/llm.py`'s `history` only lasts one conversation (it resets every time
the wake word starts a new one) -- `brain/memory.py` is separate, persistent
storage that carries across conversations, in two tiers, both under the
gitignored `household_memory/` directory at the repo root (real household
data, not something that belongs in a public repo):

- **`core.txt`** -- small facts/preferences (names, allergies, house rules).
  Plain text, one per line, injected into *every* request's system prompt, so
  Claude doesn't need a tool to recall it out loud. Claude manages this
  itself: a `remember` tool to save something worth keeping, a `forget` tool
  to remove it. Curate by hand too, anytime -- open `household_memory/core.txt`
  over SSH in any text editor and edit or delete a line directly. Keep this
  tier small on purpose (a handful to a few dozen facts) -- everything in it
  costs tokens on every single request, even ones that have nothing to do
  with it.
- **`reference/`** -- anything bigger: recipes, family member details,
  birthdays, school/activity schedules, whatever else. *Not* injected into
  every prompt (would bloat cost/latency for unrelated turns) -- instead
  Claude searches it on demand with the `search_household_info` tool, which
  does a plain per-word, case-insensitive search across every file under
  `household_memory/reference/` and returns whole matching files. Add files
  here directly, in whatever format/structure makes sense -- there's
  deliberately no schema imposed yet.

Open questions, intentionally not decided yet since there's no real data to
design them against: how birthdays/schedules should actually be structured
(plain text vs. CSV vs. native Excel -- the last would need adding `openpyxl`
as a dependency), and whether the reference tier ever needs its own
voice-driven write tool (right now it's curated by hand, matching how it's
actually expected to be populated -- as files, not fact-by-fact through
conversation).

## Running as a background service

`systemd/pi-voice-assistant.service` runs `wake_word_daemon.py` persistently
— survives SSH disconnects, restarts on crash. It's a **user-level** systemd
unit (`systemctl --user`, not `sudo systemctl`) rather than a system unit,
specifically so it shares your normal login session's PipeWire audio setup
— a root/system-level service would not have access to that per-user audio
session at all.

**Important: don't enable it to auto-start on boot.** Only the [Shabbat/Yom
Tov gate checker](#shabbatyom-tov-gating) below should ever start this
service — that way a fresh boot defaults to "not listening" until the
checker has actually confirmed it's safe to run, rather than blindly
listening from the moment the Pi powers on.

Install (but don't enable) it:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/pi-voice-assistant.service ~/.config/systemd/user/
systemctl --user daemon-reload
loginctl enable-linger $USER   # lets user-level services run without an active login session
```

Note `ExecStart` uses the **absolute path** to `uv`
(`/home/<PI_USER>/.local/bin/uv`) rather than relying on `PATH` — systemd
services start with an even more minimal environment than a non-interactive
SSH session, so the same PATH issue described above applies here too, just
with no shell to add a workaround line to.

This service does need `.env` (see [Wake word](#wake-word-alexa--talking-to-claude)
above) — `WorkingDirectory` is set to the repo root, so `python-dotenv` finds
it there automatically, same as running it manually. `.env` is gitignored, so
`git pull`/`update-pi.sh` never touches it — create it directly on the Pi
once, by hand, the same way you set up `.pi-config`.

Check on it:

```bash
systemctl --user status pi-voice-assistant
journalctl --user -u pi-voice-assistant -f   # watch for "Wake word detected: alexa"
```

Manually start/stop it (e.g. before running `uv run main.py test`, so both
aren't fighting over the mic at once — though normally the gate checker is
what starts/stops it, not you directly):

```bash
systemctl --user stop pi-voice-assistant
systemctl --user start pi-voice-assistant
```

## Shabbat/Yom Tov gating

Design doc: `docs/specs/shabbat-gating.md`. The device must not operate
during Shabbat or Yom Tov — this is enforced by a separate checker
(`shabbat/gate.py`) that runs every minute via its own systemd timer,
independent of the wake-word daemon's own code, so a bug in one doesn't
compromise the other. It:

- Stops `pi-voice-assistant` (the wake-word daemon) at candle-lighting and
  starts it again at havdalah, using cached [Hebcal](https://www.hebcal.com/)
  data (candle-lighting, havdalah, and Yom Tov days) for your configured
  location.
- Plays spoken warnings at 15/10/5 minutes before candle-lighting, plus
  distinct entrance/exit announcements — with separate Hebrew wording for
  Shabbat vs. Yom Tov (see the spec for why that distinction matters).
- **Fails closed on any uncertainty**: if the system clock isn't confirmed
  NTP-synced, or the cached zmanim data is missing/stale beyond 30 days, it
  gates the device *off* rather than risk operating during Shabbat.
- Gates on full Yom Tov days only (Rosh Hashana, Yom Kippur, Pesach I/VII,
  Shavuot, Sukkot I, Shmini Atzeret) — Chol HaMoed (the intermediate days of
  Pesach/Sukkot) is treated as a regular day.

**One-time setup — set your location** (personal, gitignored, same pattern
as `.pi-config`'s other values):

```bash
# add to .pi-config:
SHABBAT_GEONAMEID=<your city's Hebcal geonameid>
SHABBAT_ISRAEL=true   # or false for Diaspora (affects one vs. two days of Yom Tov)
```

Find your geonameid via Hebcal's city search: `curl -s "https://www.hebcal.com/complete.php?q=YourCity"`

**Install the gate checker** (also a user-level unit, for the same
audio-session reason as above):

```bash
cp systemd/pi-voice-assistant-gate.service systemd/pi-voice-assistant-gate.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pi-voice-assistant-gate.timer
```

Check on it:

```bash
systemctl --user list-timers pi-voice-assistant-gate.timer
journalctl --user -u pi-voice-assistant-gate -f
```

Run it once manually to test immediately rather than waiting for the timer:

```bash
uv run python -m shabbat.gate
```

## Roadmap (not in this milestone)

- Custom-trained wake words ("Menachem Mendel" / "Mendy" via openWakeWord) —
  `wake_word_daemon.py`'s "Alexa" is a pretrained placeholder, not the real thing
- Confirm the full Claude conversation loop end-to-end on real hardware once
  a working Pi is available again (see [Wake word](#wake-word-alexa--talking-to-claude))
- Evaluate dual wake words (one per language: English/Hebrew) as a replacement
  for `brain/stt.py`'s forced-language-per-session approach. Would fully
  eliminate any STT language-detection dependency, but needs a real
  benchmark first: confirm two concurrent openWakeWord models don't reintroduce
  the audio "input overflow" problem this project already hit once on the Pi 4
  (real-time callback + CPU-bound inference contention) before committing to
  training a second custom wake word
- Timers
- Spotify control
- Voice-queryable zmanim ("when does Shabbat start?") — the underlying Hebcal
  data already exists for gating (see [Shabbat/Yom Tov
  gating](#shabbatyom-tov-gating)), just not exposed as a spoken query yet
