# pi-voice-assistant

A Raspberry Pi 4 voice assistant. `wake_word_daemon.py` does real free-form
conversation: "Alexa" (English) or "Mendy" (Hebrew) triggers a recording
of your question, which goes through an LLM (such as Claude or Gemini) and
comes back as spoken audio — in whichever of English/Hebrew you actually
spoke. See [Wake word](#wake-word-alexa--mendy--talking-to-claude) below.

> **What's actually been tested:** the full record → save → playback round
> trip has been verified on the actual target hardware — a **Raspberry Pi 4
> Model B** with a real **HyperX QuadCast S** USB microphone — over Ethernet.
> It was also verified earlier on a development machine during initial
> implementation. Beyond the QuadCast S, the project assumes a **generic USB
> microphone** and a **generic Bluetooth speaker**: nothing here depends on
> QuadCast- or vendor-specific drivers, but no other specific mic/speaker
> model has been tried, and Bluetooth speaker output hasn't been verified
> end-to-end yet (see [Bluetooth speaker
> setup](#bluetooth-speaker-setup)). The [wake
> word](#wake-word-alexa--mendy--talking-to-claude)
> pipeline is verified up to the mic-capture stage (confirmed real audio
> flows through at the correct format via direct RMS measurement) but actual
> "Alexa" **detection with a real human voice is not yet confirmed** — a
> synthetic TTS test voice didn't trigger it, which may just mean the model
> needs real speech rather than indicating a bug.

## Hardware

- Raspberry Pi 4 Model B
- A USB microphone — prefers the **reSpeaker XVF3800 4-Mic Array** by name
  when it's plugged in, otherwise falls back to the **HyperX QuadCast S**
  (`audio_check/config.py` → `input_name_hint`, tried in order); any generic
  USB microphone works too, change the hint(s) or pass `--device-hint` for a
  different one
- A speaker — wired (3.5mm/USB/HDMI) or a generic Bluetooth speaker. Bluetooth
  speakers need pairing first — see [Bluetooth speaker
  setup](#bluetooth-speaker-setup)

## Project layout

```
pi-voice-assistant/
├── main.py              # entry point
├── wake_word_daemon.py  # always-on: "Alexa" -> record question -> Claude -> spoken reply
├── telegram_bot_daemon.py  # always-on: Telegram chat -> same Claude brain -> text reply
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
│   ├── gcal.py          # Mendy's calendar -- Google Calendar via a service account
│   ├── reminders.py     # background poller that speaks calendar reminders aloud, unprompted
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
│   ├── pi-telegram-bot.service           # telegram_bot_daemon.py (user unit)
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

## Wake word ("Alexa" / "Mendy") + talking to Claude

`wake_word_daemon.py` listens continuously for **two** wake words at once:
**"Alexa"** and **"Mendy"** (the custom-trained "Menachem Mendel" model at
[`models/mendy.onnx`](models/mendy.onnx)). Whichever one you say determines
which language the *first* turn of that conversation is transcribed in —
Alexa for English, Mendy for Hebrew — deterministically, instead of guessing
from audio alone. Follow-up turns within the same conversation still
auto-detect language fresh per utterance (so you can switch languages
mid-conversation), since only the wake word itself is a reliable enough
signal to skip that detection.

This exists because per-utterance English/Hebrew auto-detection alone proved
unreliable in both directions — confirmed in testing: a quick "מה השעה"
(what time is it) got misheard as the English name "Masha," and "play
Summertime Sadness" got misheard as Hebrew phonetic gibberish. Which wake
word you used to start the conversation doesn't have that ambiguity.

When either wake word fires: plays a short acknowledgment chime, records ~6
seconds of your question, transcribes it, sends it to Claude, and speaks the
reply back — in whichever language you spoke. This uses
[openWakeWord](https://github.com/dscripka/openWakeWord)'s free, fully
open-source pretrained "alexa" model for English wake-word detection — **no
account, no API key, no signup** required for that part, unlike Porcupine
(the original choice for this proof-of-concept using its "jarvis" keyword,
until Picovoice discontinued its free tier in June 2026 and replaced it with
a 7-day trial) — alongside the custom "mendy" model for Hebrew.

**Mendy's evaluation** (2,000 held-out positive + 2,000 held-out negative
clips from its own training Colab, none seen during training, scored at the
same `DETECTION_THRESHOLD = 0.6` `wake_word_daemon.py` actually uses):
detects "Mendy" **90.5%** of the time (avg score 0.881), false-triggers on
unrelated speech **2.9%** of the time (avg score 0.034). For comparison, the
mature pretrained "alexa" model scored on those same clips: 0% (correctly
never mistakes "Mendy" for "Alexa") and **0.2%** false-trigger rate on the
same negative clips — about 14x lower than Mendy's, expected since alexa was
trained on 30,000+ hours of negative audio versus Mendy's 10,000 training
clips. These numbers are from synthetic TTS voices in the training pipeline's
own test split, not real speech through the actual mic — still worth
confirming against real voices/room acoustics via `wakeword_bench` (below)
before fully trusting the false-trigger rate day to day.

If `models/mendy.onnx` is ever missing, `wake_word_daemon.py` falls back to
openWakeWord's pretrained **"hey_jarvis"** model as a placeholder Hebrew
trigger — its English meaning is irrelevant, it's just a distinct, reliable
acoustic trigger, same role "alexa" plays for English.

**Training your own wake word:** not something to do locally in this repo's
own dev environment -- openWakeWord's pretrained models are trained on
30,000+ hours of negative audio (speech/noise/music) to avoid false triggers,
and the training code needs a multi-GB PyTorch + TensorFlow stack
(`pip install openwakeword[full]`) neither of which is practical to pull into
a normal dev machine just to try one word. Do it via openWakeWord's own
free-GPU Colab notebooks instead (linked from [openWakeWord's
README](https://github.com/dscripka/openWakeWord)): the simple one-click
notebook for a quick model, or `automatic_model_training.ipynb` for a
higher-quality one.

If you want a **different** wake word than "Mendy" (or just don't want to
fight the upstream notebook's bitrot), use
[`mendy_wake_word_training.ipynb`](mendy_wake_word_training.ipynb) in this
repo instead -- it's `automatic_model_training.ipynb` pre-adapted for this
project: unnecessary TensorFlow/tflite dependencies removed (this repo only
ever loads `.onnx` models, see `wake_word_daemon.py`'s `inference_framework="onnx"`),
re-run-safe installs, production-grade sample counts instead of the upstream
notebook's quick-demo settings, and extra cells to download the trained model
and self-test it before leaving Colab. To target a different word, just
change `config["target_phrase"] = ["mendy"]` near the top to whatever you
want. Upload it via Colab's File → Upload notebook.

Either way you'll end up with a `.onnx` file. Replace `models/mendy.onnx`
with it directly, or point `.pi-config` at a different path/filename if you'd
rather keep both around:
```bash
# add to .pi-config to override the default (models/mendy.onnx):
WAKE_WORD_MODEL_PATH=/path/to/your-model.onnx
```
(see `brain/config.py`'s `WAKE_WORD_MODEL_PATH`). Delete `models/mendy.onnx`
entirely (and don't set an override) to fall back to the pretrained
"hey_jarvis" placeholder instead. Once you have a real model, expect to tune
`DETECTION_THRESHOLD` in `wake_word_daemon.py` against real speech -- a
custom model's confidence distribution won't necessarily match "alexa"'s or
"hey_jarvis"'s.

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
   HOUSEHOLD_NEARBY_AREAS=Nearby City 1, Nearby City 2 -- all a short drive away
   ```
   This nudges Claude toward the right country, but it's still an LLM: for
   anything safety-critical (e.g. a specific crisis hotline number), it's
   instructed to say so and point to local emergency services rather than
   guess, instead of confidently stating a number it isn't sure is current
   or correct for your location. `HOUSEHOLD_NEARBY_AREAS` is optional but
   worth setting if you're in a dense metro area -- without it, Claude
   over-indexed on treating HOUSEHOLD_LOCATION as the *only* local place
   (confirmed: asked about a real mall in a neighboring city, it got
   confused and invented a claim that it must be a different, unverified
   place, purely because the city name in the search results didn't match
   HOUSEHOLD_LOCATION exactly).
4. (Optional) Add family member names, in each language they're actually
   spoken/read in, so speech-to-text is biased toward recognizing them
   correctly:
   ```bash
   # add to .pi-config:
   HOUSEHOLD_FAMILY_NAMES_EN=Name1, Name2, Name3
   HOUSEHOLD_FAMILY_NAMES_HE=שם1, שם2, שם3
   ```
   Confirmed this matters: without a hint, Whisper transcribed a real family
   member's uncommon name as a completely different, unrelated-sounding
   word; with the hint, it came out correct. Passed to Groq's Whisper API as
   a `prompt` (see brain/stt.py) -- biases transcription toward that
   vocabulary without needing to match a real transcript.

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

## Mic LED patterns

The mic is a **Seeed reSpeaker XVF3800 4-Mic Array**. `mic_leds.py` drives
its LED ring via Seeed's `xvf_host` USB control tool. The device's LED
protocol only exposes 5 fixed effects (off/breath/rainbow/single-color/doa)
plus a doa base+highlight color pair — there's no per-pixel/custom-animation
command, and `rainbow` renders as a static multicolor ring rather than
anything that rotates (confirmed live against the real hardware), so these
are the closest fit within what the firmware actually supports:

- **Idle** (static rainbow) — resting look.
- **Listening** (blue base, green highlight) — the wake word just fired
  and it's actively recording your question, using `led_doa_color` with
  custom colors instead of the device's own default blue/green. This is the
  most important one: it's your only signal that it actually heard "Alexa"
  and is waiting on you.
- **Thinking** (white breathing pulse) — recording just finished; covers the
  transcribe/Claude/TTS gap until the reply starts playing. Speed tuned live
  against the real hardware (1 too slow, 4 too fast, 2 confirmed good).
  Paired with a single short chime right when this state starts (not
  repeated on tool calls anymore — two inconsistent beeps was the actual
  complaint that led to adding this state).
- **Speaking** (solid magenta) — the assistant's reply is playing.
- **Idle transition** (a brief solid white flash) — plays once when a
  conversation ends, then settles back into the idle rainbow.
- **Error** (solid orange) — reserved for wifi/API/hardware trouble: a
  failed transcription/Claude call, a recording/playback failure, or no
  mic/speaker found at startup. Held rather than timed out, since whatever
  broke may still be broken; the next successful state clears it.

Colors are constants at the top of `mic_leds.py` — tweak freely.

**Setup (per machine — Mac dev box and Pi both need this):**

1. Download the `xvf_host` binary for your platform from
   [respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY)'s
   `host_control/<platform>/` directory (`mac_arm64`, `rpi_64bit`,
   `linux_x86_64`, `jetson`, or `win32`).
2. Place it at `vendor/xvf_host/<platform>/xvf_host` (gitignored — it's a
   third-party binary, not something to commit) and `chmod +x` it.
3. Verify with `./vendor/xvf_host/<platform>/xvf_host VERSION` while the
   array is plugged in.

If the binary isn't found (or the array isn't plugged in), `mic_leds.py`
logs one warning and no-ops — LED patterns are cosmetic, never a hard
dependency of the voice pipeline. Override the binary path with the
`XVF_HOST_BIN` env var if you'd rather not use the `vendor/` layout.

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

## Mendy's calendar & reminders

A dedicated Google Calendar ("Mendy") for household reminders -- medication
schedules, appointments, anything recurring or one-time -- editable three
ways that all stay in sync automatically: the Google Calendar app on any
phone, by voice ("add antibiotics at 8am and 8pm every day for 10 days"), or
by messaging the Telegram bot. `brain/reminders.py` polls it in the
background and speaks each reminder aloud, unprompted, at its start time --
the calendar analogue of the timer alarm.

**One-time setup -- Google Calendar (service account, no interactive login
ever):**

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project, enable the **Google Calendar API**, then create a **service
   account** and download its JSON key.
2. In Google Calendar's web UI, create a new secondary calendar named
   "Mendy". Under that calendar's **Settings → Share with specific people**,
   add the service account's email (from the JSON key file,
   `...@...iam.gserviceaccount.com`) with **"Make changes to events"**
   permission.
3. Copy that calendar's id (**Settings → Integrate calendar → Calendar ID**,
   looks like an email address) into `.pi-config`, and the JSON key's path
   into `.env`:

```bash
# add to .pi-config:
MENDY_CALENDAR_ID=<calendar id>

# add to .env:
GOOGLE_SERVICE_ACCOUNT_FILE=/home/<PI_USER>/<PI_DIR>/.gcal-service-account.json
```

Place the downloaded key at that path (gitignored -- never commit it), then
verify access:

```bash
uv run python -m brain.gcal
```

**One-time setup -- Telegram bot:**

1. Message [@BotFather](https://t.me/BotFather) on Telegram, `/newbot`, and
   copy the token it gives you into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Message your new bot once from each phone that should be allowed to use
   it, then find each chat's numeric id (e.g. via
   [@userinfobot](https://t.me/userinfobot)) and add them to `.pi-config`:

```bash
# add to .pi-config:
TELEGRAM_ALLOWED_CHAT_IDS=111111111,222222222
```

Anyone not on that list gets a refusal and no tool access at all -- see
`telegram_bot_daemon.py`.

**Install the Telegram bot as a background service** (same user-level-unit
reasoning as `pi-voice-assistant.service` below, though this one doesn't
touch audio):

```bash
cp systemd/pi-telegram-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pi-telegram-bot
```

Reminders fire from inside `wake_word_daemon.py` itself (see
`brain/reminders.py`'s module docstring for why it can't run as a separate
process) -- no extra service to install for that part.

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

This service does need `.env` (see [Wake
word](#wake-word-alexa--mendy--talking-to-claude)
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
(`shabbat/gate.py`) that runs every minute, independent of the wake-word
daemon's own code, so a bug in one doesn't compromise the other. **Works on
Mac, Linux, or Windows** — it prefers systemd where available (the real Pi
deployment, see below, which also gets crash-restart and start-at-boot for
free), and otherwise manages `wake_word_daemon.py` directly via a pidfile
(`wake_word_daemon.pid`, gitignored) and `psutil`, so gating still works
identically wherever it's actually run. It:

- Stops the wake-word daemon at candle-lighting and starts it again at
  havdalah, using cached [Hebcal](https://www.hebcal.com/) data
  (candle-lighting, havdalah, and Yom Tov days) for your configured location.
- Plays spoken warnings at 15/10/5 minutes before candle-lighting, plus
  distinct entrance/exit announcements — with separate Hebrew wording for
  Shabbat vs. Yom Tov (see the spec for why that distinction matters).
- Right after the entrance/exit announcement, also speaks a dynamic digest of
  any [Mendy calendar](#mendys-calendar--reminders) reminders due during that
  Shabbat/Yom Tov window — a heads-up before candle-lighting, a catch-up
  right after havdalah — since medication schedules don't pause just because
  the device is gated off. Best-effort: a calendar/network failure here is
  swallowed and never affects gating itself.
- **Fails closed on any uncertainty**: if the system clock isn't confirmed
  synced (checked by querying a real NTP server directly — `shabbat/ntp.py`
  — rather than an OS-specific "am I synced" tool, so this is also
  cross-platform), or the cached zmanim data is missing/stale beyond 30 days,
  it gates the device *off* rather than risk operating during Shabbat.
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

- ~~Custom-trained wake word ("Menachem Mendel" / "Mendy" via
  openWakeWord)~~ Done — `models/mendy.onnx` is trained and wired in as the
  default Hebrew trigger, replacing the "hey_jarvis" placeholder (which is
  now only a fallback if that file is ever missing). Evaluated against 2,000
  held-out synthetic clips (90.5% detection, 2.9% false-trigger rate — see
  [Wake word](#wake-word-alexa--mendy--talking-to-claude)); still needs a
  live `wakeword_bench` run against real voices/room acoustics for full
  confidence
- Confirm the full Claude conversation loop end-to-end on real hardware once
  a working Pi is available again (see [Wake word](#wake-word-alexa--mendy--talking-to-claude))
- ~~Evaluate dual wake words...~~ Done — "Alexa"/"Mendy" now determine
  turn-1 language deterministically (see [Wake
  word](#wake-word-alexa--mendy--talking-to-claude)). Confirmed on this
  dev Mac that a single `Model(wakeword_models=[...])` instance runs both
  wake words off one shared embedding/melspectrogram pass (only the small
  final classifier head runs per-model, not the whole pipeline), so this is
  cheaper than two full concurrent models -- but still **not yet verified on
  actual Pi 4 hardware**, where the original "input overflow" problem
  happened. Confirm real-time performance there before relying on this.
- Timers
- Spotify control
- Voice-queryable zmanim ("when does Shabbat start?") — the underlying Hebcal
  data already exists for gating (see [Shabbat/Yom Tov
  gating](#shabbatyom-tov-gating)), just not exposed as a spoken query yet
