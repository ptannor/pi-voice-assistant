#!/usr/bin/env python3
"""Wake word daemon: listens for two wake words -- "Alexa" for English,
"Mendy" (the custom-trained "Menachem Mendel" model at models/mendy.onnx)
for Hebrew -- then records a question, sends it through Claude, and speaks
the reply back. Which wake word triggered the conversation determines the
first turn's language deterministically (see _load_wake_word_model);
follow-up turns within the same conversation still re-detect language per
utterance, since a conversation may switch languages mid-stream.

Uses openWakeWord's free, fully open-source pretrained "alexa" model for
English (no account, no API key, no signup) alongside the custom "mendy"
model for Hebrew. If models/mendy.onnx is ever missing, this falls back to
openWakeWord's pretrained "hey_jarvis" model as a placeholder Hebrew trigger
(its English meaning is irrelevant, it's just a distinct acoustic trigger).
"""
from __future__ import annotations

import collections
import json
import os
import queue
import sys
import tempfile
import time
import wave
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

# Global state for Spotify playback status to adjust wake word threshold dynamically
spotify_is_playing = False

def _poll_spotify_status():
    global spotify_is_playing
    try:
        from brain import spotify
    except Exception:
        return
    while True:
        try:
            spotify_is_playing = spotify.is_playing()
        except Exception:
            spotify_is_playing = False
        time.sleep(3.0)

import openwakeword
import sounddevice as sd
from openwakeword.model import Model

import mic_leds
from brain.audio_focus import Channel, manager as focus
from audio_check.config import DEFAULT_CONFIG
from audio_check.devices import Device, find_input_device, find_output_device
from audio_check.errors import AudioCheckError, PlaybackFailed, RecordingFailed
from audio_check.player import play_wav, play_wav_async
from audio_check.recorder import SILENCE_RMS_THRESHOLD, record_until_silence
from brain.config import WAKE_WORD_MODEL_PATH
from brain.llm import STOP_WORDS, BrainError, ask
from brain.respond import speak_reply, speak_reply_chunks
from brain.reminders import start as start_reminders
from brain.stt import TranscriptionError, transcribe

ACK_WAV = Path(__file__).parent / "assets" / "chime.wav"
GOODBYE_WAV = Path(__file__).parent / "assets" / "goodbye_chime.wav"
# Written at startup, removed on clean exit -- lets shabbat/gate.py check
# liveness/terminate this process directly on platforms without systemd
# (see its enforce_gate). Gitignored, same as the other runtime state files.
PIDFILE = Path(__file__).parent / "wake_word_daemon.pid"
# Played (fire-and-forget) the moment a turn needs a tool call -- e.g.
# web_search, which dominates turn latency (~3.6-4s) on most factual/local
# questions. Without this, the assistant sits silent that whole time; this
# gives an acknowledgment within ~1s instead, which is what actually makes
# Alexa feel responsive (see the design review that ruled out full response
# streaming as not worth the complexity/regression risk).
THINKING_WAV = Path(__file__).parent / "assets" / "thinking.wav"

# One JSON object per turn, appended (not overwritten) -- lets latency be
# analyzed across many runs/sessions later instead of only whatever's still
# in a terminal's scrollback. Gitignored: durations only, but still runtime
# data from a household voice assistant, not something to publish.
LOG_PATH = Path(__file__).parent / "logs" / "latency.jsonl"


def _log_turn(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


# Used as record_until_silence's lead_in_seconds -- someone who starts
# talking right as the chime plays (instead of waiting for it to finish)
# was getting clipped entirely, since recording only used to start once
# play_wav() returned. See _handle_conversation below.
ACK_DURATION_SECONDS = _wav_duration_seconds(ACK_WAV)
ENGLISH_WAKE_WORD = "alexa"
HEBREW_WAKE_WORD = "hey_jarvis"  # fallback if models/mendy.onnx is ever missing -- see _load_wake_word_model below
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80ms at 16kHz -- openWakeWord's recommended chunk size
DETECTION_THRESHOLD = 0.6  # Default threshold when music is not playing, to prevent false triggers
# Wake words that fire against near-silent ambient audio -- confirmed live
# ("Wake word detected: alexa (score=0.60)" with nobody actually speaking) --
# are the model matching quiet background noise, not real speech. Reuses
# audio_check/recorder.py's own empirically-tuned speech-vs-noise threshold
# (background noise ~90-160, speech spikes 700-4000+) as a sanity gate: a
# detection only counts if *some* chunk in the last second or so actually
# reached that level, i.e. someone was plausibly talking nearby. Checked over
# a short rolling window, not just the exact triggering chunk, since a wake
# word's own quieter syllables (a trailing consonant, etc.) can dip below
# this on their own mid-utterance.
WAKE_WORD_RMS_HISTORY_CHUNKS = 12  # ~1s of CHUNK_SAMPLES (80ms) chunks
COOLDOWN_SECONDS = 2.0  # ignore re-triggers right as we resume listening
# How long to wait for the user to start talking before giving up.
INITIAL_QUERY_TIMEOUT = 4.0
FOLLOW_UP_TIMEOUT = 3.5  # long enough for a real follow-up, short enough to limit exposure to ambient noise
MAX_FOLLOW_UP_TURNS = 5  # safety cap -- require a fresh "Alexa" after a while regardless
# Tool names (matched as substrings against ask_timeline's "tool:<name>"
# stage labels) whose reply is silent on success (see brain/llm.py) but
# where an immediate follow-up is likely enough that the conversation
# should stay open anyway -- e.g. right after skipping a track, "no, a
# different one" or "what's playing" is a far more likely next utterance
# than a fresh wake word. Confirmed a real gap: skip_track's silent reply
# doesn't end in "?", so the keep-listening check below used to close the
# conversation immediately after every skip.
MUSIC_FOLLOW_UP_TOOL_KEYWORDS = ("play_music", "search_music", "skip_track", "seek_music", "get_current_track")
# How long after a conversation ends a fresh "Alexa" is still treated as a
# continuation of it (same history) rather than starting blank. Confirmed
# needed: a closed-answer reply (not ending in "?") ends the
# conversation by design, but the user often says "Alexa" again seconds later
# to ask an obvious follow-up ("what about showtimes for that one") -- without
# this, that follow-up got answered with zero memory of what was just
# discussed, producing an answer about unrelated cinemas.
CONTINUATION_WINDOW_SECONDS = 90.0

# Explicit signals the user is done, checked against what they just said --
# end the conversation immediately rather than waiting on the follow-up
# timeout/turn cap.
_CLOSING_PHRASES_EN = (
    "thanks", "thank you", "that's all", "that's it", "goodbye", "bye",
    "nothing else", "i'm done", "im done", "that'll be all", "no thanks",
    "no thank you", "nope", "nah", "forget it", "never mind", "nevermind",
    "i'm good", "im good", "that's fine", "all good", "we're good",
)
_CLOSING_PHRASES_HE = (
    "תודה", "זהו", "זה הכל", "להתראות", "ביי", "נגמר",
    "לא תודה", "לא צריך", "עזוב", "בסדר גמור", "מספיק",
)


def _said_closing_phrase(text: str, language: str) -> bool:
    lowered = text.lower()  # no-op for Hebrew (no letter case), normalizes English
    phrases = _CLOSING_PHRASES_HE if language == "he" else _CLOSING_PHRASES_EN
    return any(phrase in lowered for phrase in phrases)


def _load_wake_word_model() -> tuple[Model, dict[str, str]]:
    """Returns (model, wake_word_language) -- wake_word_language maps every
    wake word key the model listens for to the language ("en"/"he") that
    saying it selects for the first turn of a conversation. This lets turn 1
    skip the ambiguous dual-language STT confidence comparison entirely
    (confirmed unreliable in BOTH directions -- see brain/stt.py's
    transcribe() docstring) in favor of a deterministic signal: which wake
    word was actually said. Follow-up turns still re-detect language per
    utterance, since a conversation may switch languages mid-stream.

    WAKE_WORD_MODEL_PATH (see brain/config.py) points at a custom-trained
    Hebrew model -- models/mendy.onnx by default, trained via openWakeWord's
    own Colab notebook (30,000+ hours of negative audio and a multi-framework
    torch+tensorflow training stack make local training impractical here;
    see the README's Wake word section for the actual recipe). If that file
    is ever missing, HEBREW_WAKE_WORD falls back to the pretrained
    "hey_jarvis" model as a placeholder Hebrew trigger -- its English meaning
    is irrelevant, it's just a distinct, reliable acoustic trigger, same role
    "alexa" plays for English.
    """
    if WAKE_WORD_MODEL_PATH:
        hebrew_key = Path(WAKE_WORD_MODEL_PATH).stem
        openwakeword.utils.download_models(model_names=[ENGLISH_WAKE_WORD])
        model = Model(wakeword_models=[WAKE_WORD_MODEL_PATH, ENGLISH_WAKE_WORD], inference_framework="onnx")
        return model, {hebrew_key: "he", ENGLISH_WAKE_WORD: "en"}

    openwakeword.utils.download_models(model_names=[ENGLISH_WAKE_WORD, HEBREW_WAKE_WORD])
    # Force onnx: the tflite_runtime wheel available on some platforms (e.g. the
    # Pi's aarch64 build) is compiled against NumPy 1.x and breaks under NumPy 2.x
    # ("_ARRAY_API not found"). onnxruntime works correctly on both dev machine and Pi.
    model = Model(wakeword_models=[ENGLISH_WAKE_WORD, HEBREW_WAKE_WORD], inference_framework="onnx")
    return model, {ENGLISH_WAKE_WORD: "en", HEBREW_WAKE_WORD: "he"}


def _listen_for_wake_word(
    model: Model, wake_word_language: dict[str, str], in_device: Device, last_trigger: float
) -> tuple[float, str]:
    """Block until any wake word is detected; return (new last_trigger time,
    the wake word key that fired) -- the caller uses the latter to look up
    which language that wake word selects (see _load_wake_word_model).

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

    # Reset model states before listening for a fresh wake word trigger
    model.reset()
    recent_rms: collections.deque = collections.deque(maxlen=WAKE_WORD_RMS_HISTORY_CHUNKS)

    with sd.InputStream(
        device=in_device.index,
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
        latency='high',
        callback=callback,
    ):
        while True:
            pcm = audio_queue.get()
            # Always fed to the model (keeps its internal streaming state
            # correct) regardless of the RMS gate below -- only the decision
            # to *act* on a firing score is gated, not the model's own input.
            recent_rms.append(float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))))
            prediction = model.predict(pcm)
            now = time.monotonic()
            current_threshold = 0.35 if spotify_is_playing else DETECTION_THRESHOLD
            for key in wake_word_language:
                score = prediction.get(key, 0.0)
                if score > current_threshold and (now - last_trigger) > COOLDOWN_SECONDS:
                    if max(recent_rms) < SILENCE_RMS_THRESHOLD:
                        print(
                            f"Ignoring '{key}' (score={score:.2f}) -- no speech-level "
                            f"audio nearby (max RMS {max(recent_rms):.0f})",
                            flush=True,
                        )
                        continue
                    print(f"Wake word detected: {key} (score={score:.2f})", flush=True)
                    return now, key


def _play_wav_with_barge_in(
    filepath: Path,
    in_device: Device,
    out_device: Device,
    model,
    wake_word_keys,
) -> bool:
    """Plays a WAV file on out_device while listening to in_device for the wake word.
    Returns True if a barge-in wake word was detected, False otherwise.
    """
    from audio_check.player import _load_wav, _playback_lock
    try:
        target_sr = int(out_device.default_samplerate)
        audio, sample_rate = _load_wav(filepath, target_sr=target_sr)
    except Exception as exc:
        print(f"Error loading WAV for playback: {exc}", file=sys.stderr)
        return False

    # Reset the stateful wake word model's hidden states so it forgets the previous trigger
    model.reset()

    audio_queue: queue.Queue = queue.Queue()
    def callback(indata, frames, time_info, status):
        audio_queue.put(indata[:, 0].copy())

    # _playback_lock (see audio_check/player.py) serializes every sd.play()/
    # sd.wait() call across threads -- sounddevice manages a single *global*
    # default output stream, so two threads calling it concurrently race on
    # shared state instead of queueing cleanly. This function used to call
    # sd.play()/sd.stop()/sd.wait() directly, unlocked -- the one caller not
    # covered by that fix, and confirmed able to reproduce the exact hang
    # (stuck in native PortAudio, not even Ctrl-C-interruptible) the lock was
    # built to prevent: a timer/reminder alarm's play_wav() landing mid-reply
    # here. Holding the lock for this function's whole body (not just the
    # sd.play() call) is safe for the alarm-preemption path below -- sd.stop()
    # halts this function's own playback almost immediately, so a blocked
    # alarm thread only waits that briefly, not for the lock to free up on
    # its own schedule.
    barge_in = False
    with _playback_lock:
        # Start playback asynchronously with higher latency to prevent CPU/GIL starvation static noise
        sd.play(audio, samplerate=sample_rate, device=out_device.index, latency='high')
        duration = len(audio) / sample_rate
        start_time = time.monotonic()

        with sd.InputStream(
            device=in_device.index,
            channels=1,
            samplerate=SAMPLE_RATE,
            dtype="int16",
            blocksize=CHUNK_SAMPLES,
            latency='high',
            callback=callback,
        ):
            while time.monotonic() - start_time < duration:
                # A higher-priority channel (a timer alarm) firing mid-reply must
                # preempt this spoken reply immediately -- the reply is abandoned,
                # not queued behind the alarm. See brain/audio_focus.py.
                if focus.is_preempted(Channel.DIALOG):
                    print("Reply preempted by a higher-priority alarm; stopping playback.", flush=True)
                    sd.stop()
                    break
                try:
                    pcm = audio_queue.get(timeout=0.1)
                    prediction = model.predict(pcm)
                    # Any wake word interrupts -- not just whichever one started
                    # this conversation, since the user may address the assistant
                    # in either language mid-reply.
                    key, score = max(
                        ((k, prediction.get(k, 0.0)) for k in wake_word_keys),
                        key=lambda pair: pair[1],
                    )
                    # Lower threshold (0.35) during active playback to make it easier to
                    # interrupt the assistant's own voice feedback from the speakers.
                    if score > 0.35:
                        print(f"Barge-in detected: {key} (score={score:.2f})! Interrupting playback.", flush=True)
                        sd.stop()
                        barge_in = True
                        break
                except queue.Empty:
                    continue

        if not barge_in:
            sd.wait()
    return barge_in


def _handle_conversation(
    in_device: Device,
    out_device: Device,
    model,
    wake_word_keys,
    initial_history: list[dict] | None = None,
    conversation_language: str | None = None,
) -> list[dict] | None:
    """Returns `history` as it stood when the conversation ended, so `main()`
    can offer it to the *next* call as a continuation if a fresh wake word
    comes in soon enough (see CONTINUATION_WINDOW_SECONDS) -- otherwise this
    always starts blank.

    `conversation_language` (from which wake word triggered this call -- see
    _load_wake_word_model) is applied to EVERY turn below, not just the
    first -- by product decision, a conversation's language never changes
    mid-stream; switching languages requires a fresh wake word (which starts
    a new call to this function). This also means every turn only needs one
    Groq transcription call instead of two (see brain/stt.py's transcribe()
    docstring for the dual-detect fallback this skips).
    """
    # Acquire the DIALOG audio-focus channel for the duration of this
    # conversation. This pauses (and snapshots) any Spotify music so the mic
    # can hear the user clearly and so the exact track/position can be resumed
    # afterward, and -- if the user woke up on a ringing alarm -- dismisses that
    # alarm. See brain/audio_focus.py; the earlier ad-hoc was_playing/was_alarm/
    # stop_called_in_session flags are now all handled by the focus manager.
    focus.acquire(Channel.DIALOG)
    preempted = False
    errored = False

    history = initial_history
    timeout = INITIAL_QUERY_TIMEOUT
    turns = 0
    while turns < MAX_FOLLOW_UP_TURNS:
        turns += 1
        query_wav = Path(tempfile.mktemp(suffix=".wav"))
        try:
            # Start listening *while* the chime plays, instead of waiting for
            # it to finish first -- someone who starts talking right on the
            # chime (not after it) was getting the start of their question
            # clipped entirely, since recording used to only begin once
            # play_wav() returned. lead_in_seconds shields the chime's own
            # sound from being mistaken for speech (or for the user going
            # silent right after it), while still capturing anything they
            # actually say during that window. This is not the same as the
            # reply-audio barge-in that was tried and reverted elsewhere in
            # this file: that self-triggered on bleed from a long, unknown-
            # content TTS reply with no AEC to tell it apart from the user's
            # voice. This chime is short, fixed, and known in advance, and is
            # never treated as speech itself -- only real speech detected
            # right after it gets kept.
            mic_leds.enter_listening()
            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:
                record_future = pool.submit(
                    record_until_silence,
                    in_device,
                    query_wav,
                    SAMPLE_RATE,
                    1,
                    initial_timeout=timeout,
                    lead_in_seconds=ACK_DURATION_SECONDS,
                )
                ack_future = pool.submit(play_wav, ACK_WAV, out_device)
                recorded = record_future.result()
                ack_future.result()
            t1 = time.monotonic()
            if recorded is None:
                break  # nothing said -- end the conversation, back to wake-word listening

            # One short, gentle beep marking "finished listening" -- not
            # repeated on tool calls (see ask() below) since the white
            # breathing "thinking" LED now covers the whole transcribe/
            # Claude/TTS gap on its own; two inconsistent beeps was the
            # actual complaint, not too few.
            mic_leds.enter_thinking()
            play_wav_async(THINKING_WAV, out_device)

            # Every turn (not just the first) uses the deterministic language
            # from whichever wake word triggered this conversation -- see
            # this function's docstring and brain/stt.py's transcribe().
            stt_mode = "wake_word" if conversation_language else "dual"
            text, language = transcribe(query_wav, conversation_language=conversation_language)
            t2 = time.monotonic()
            print(f"Heard ({language}): {text}", flush=True)
            if not text:
                break

            reply, history, ask_timeline = ask(
                text, language, history,
                out_device=out_device,
            )
            t3 = time.monotonic()
            print(f"Claude: {reply}", flush=True)

            # Decide whether to suppress the pre-conversation snapshot resume
            # after this conversation. Tell "stop the music" apart from "stop
            # the alarm": the latter must NOT suppress the music resume.
            #   * If we woke up on a ringing alarm, any "stop" targets the alarm
            #     -> leave the music to resume (dialog_opened_on_alert short-
            #     circuits everything else).
            #   * An explicit stop_music tool -> the user stopped the music.
            #   * A bare "stop" with no timer/alarm in play -> stop the music.
            #   * cancel_timer -> stops the timer only; music still resumes.
            #   * play_music/skip_track/seek_music ran -> the user just
            #     deliberately changed what's playing; resuming the snapshot
            #     from *before* this conversation started would silently
            #     revert that change. Confirmed live: "next song" moved to a
            #     new track, then jumped back to the original song when the
            #     conversation ended and the stale snapshot resumed over it.
            lower_text = (text or "").lower()
            said_stop = any(w in lower_text for w in STOP_WORDS)
            stop_music_ran = any("stop_music" in stage for stage, _ in ask_timeline)
            cancel_timer_ran = any("cancel_timer" in stage for stage, _ in ask_timeline)
            content_changed_ran = any(
                stage.startswith("tool:") and any(kw in stage for kw in ("play_music", "skip_track", "seek_music"))
                for stage, _ in ask_timeline
            )
            if focus.dialog_opened_on_alert():
                pass
            elif stop_music_ran or content_changed_ran:
                focus.suppress_resume()
            elif said_stop and not cancel_timer_ran and not focus.alert_active():
                focus.suppress_resume()

            # Synthesize reply to WAV chunks -- pass the conversation's
            # actual language explicitly (ask() now guarantees the reply
            # matches it) rather than letting speak_reply_chunks re-detect
            # from the text, which can be fooled by a quoted phrase.
            chunks, t_first_audio = speak_reply_chunks(reply, language=language)

            # Play each chunk with barge-in
            mic_leds.enter_speaking()
            barge_in = False
            for wav in chunks:
                barge_in = _play_wav_with_barge_in(wav, in_device, out_device, model, wake_word_keys)
                wav.unlink(missing_ok=True)
                if focus.is_preempted(Channel.DIALOG):
                    preempted = True
                    break
                if barge_in:
                    break

            t4 = time.monotonic()
            perceived = (t3 - t1) + t_first_audio
            ask_breakdown = ", ".join(f"{stage}={seconds:.1f}s" for stage, seconds in ask_timeline)
            print(
                f"[timing] record={t1 - t0:.1f}s transcribe={t2 - t1:.1f}s "
                f"ask={t3 - t2:.1f}s ({ask_breakdown}) first_audio={t_first_audio:.1f}s "
                f"total_speak={t4 - t3:.1f}s perceived={perceived:.1f}s" + (" [barge-in interrupted]" if barge_in else ""),
                flush=True,
            )
            _log_turn({
                "ts": time.time(),
                "turn": turns,
                "language": language,
                "stt_mode": stt_mode,
                "record_s": round(t1 - t0, 3),
                "transcribe_s": round(t2 - t1, 3),
                "ask_s": round(t3 - t2, 3),
                "ask_breakdown": [{"stage": stage, "seconds": round(seconds, 3)} for stage, seconds in ask_timeline],
                "first_audio_s": round(t_first_audio, 3),
                "total_speak_s": round(t4 - t3, 3),
                "perceived_latency_s": round(perceived, 3),
                "query_chars": len(text),
                "reply_chars": len(reply),
            })

            if preempted:
                break  # an alarm took over -- abandon this conversation entirely

            if barge_in:
                timeout = INITIAL_QUERY_TIMEOUT
                continue

            if _said_closing_phrase(text, language):
                break  # explicit "thanks"/"bye" etc. -- end right away

            if said_stop:
                break  # "stop" means stop listening too, not just go quiet

            # Keep listening when Claude's own reply is a genuine question, or
            # when a music tool just ran (see MUSIC_FOLLOW_UP_TOOL_KEYWORDS) --
            # those replies are deliberately silent on success, but a household
            # member mid-conversation about music is very likely to immediately
            # follow up (change the song again, stop it, ask what's playing)
            # without saying the wake word again.
            music_follow_up_likely = any(
                stage.startswith("tool:") and any(kw in stage for kw in MUSIC_FOLLOW_UP_TOOL_KEYWORDS)
                for stage, _ in ask_timeline
            )
            if not reply.rstrip().endswith("?") and not music_follow_up_likely:
                break
        except (TranscriptionError, BrainError, RecordingFailed, PlaybackFailed) as exc:
            print(f"Conversation turn failed: {exc}", file=sys.stderr, flush=True)
            errored = True
            break
        except Exception as exc:
            print(f"Unexpected error handling conversation: {exc!r}", file=sys.stderr, flush=True)
            errored = True
            break
        finally:
            query_wav.unlink(missing_ok=True)
        timeout = FOLLOW_UP_TIMEOUT

    # Every exit from the loop above (silence timeout, closing phrase, turn
    # cap, or an error) falls through to here -- one clear signal that it's
    # stopped listening, distinct from the ascending "now listening" chime.
    # An error stays lit (not the usual transition-back-to-idle) since
    # whatever broke -- an API call, the network -- may still be broken.
    if errored:
        mic_leds.enter_error()
    else:
        mic_leds.enter_idle_transition()
    # Skip it when an alarm preempted us: the alarm is what should be audible,
    # not a goodbye chime played over it.
    if not preempted:
        try:
            play_wav(GOODBYE_WAV, out_device)
        except PlaybackFailed as exc:
            print(f"Goodbye chime failed: {exc}", file=sys.stderr, flush=True)

    # Release the DIALOG channel. The focus manager resumes the paused music
    # here -- exact track/position with a short fade-in -- unless it was
    # explicitly stopped or a higher-priority alarm still holds focus (e.g. we
    # were preempted, in which case the music stays paused until the alarm is
    # dismissed).
    focus.release(Channel.DIALOG)

    return history


def main() -> None:
    PIDFILE.write_text(str(os.getpid()))
    try:
        _main()
    finally:
        PIDFILE.unlink(missing_ok=True)


def _main() -> None:
    cfg = DEFAULT_CONFIG
    try:
        in_device = find_input_device(cfg.input_name_hint)
        out_device = find_output_device(cfg.output_name_hint)
    except AudioCheckError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        mic_leds.enter_error()
        sys.exit(1)

    model, wake_word_language = _load_wake_word_model()
    mic_leds.enter_idle()

    listening_for = ", ".join(f"'{key}' ({lang})" for key, lang in wake_word_language.items())
    print(
        f"Listening for {listening_for} on '{in_device.name}' (index {in_device.index})...",
        flush=True,
    )
    print(f"Responses play on '{out_device.name}' (index {out_device.index})", flush=True)

    last_trigger = 0.0
    last_conversation_end = float("-inf")
    last_history: list[dict] | None = None

    # Start the Spotify background poll thread to dynamically adjust wake word sensitivity
    threading.Thread(target=_poll_spotify_status, daemon=True).start()

    # Start the Mendy-calendar reminder poller (see brain/reminders.py) --
    # needs out_device to speak reminders, and must run in this process since
    # it acquires the ALERT audio-focus channel (brain/audio_focus.py is
    # explicitly single-process).
    start_reminders(out_device)

    while True:
        try:
            last_trigger, triggered_key = _listen_for_wake_word(model, wake_word_language, in_device, last_trigger)
        except Exception as exc:
            # Unlike _handle_conversation (which already has its own broad
            # except below), this call had no error recovery at all -- any
            # transient audio error (e.g. a USB mic dropping/re-enumerating)
            # took the whole daemon down with it. systemd's Restart=on-failure
            # masks that on the Pi, but running directly (dev, or if that unit
            # setting ever changes) it doesn't. Log and retry instead of
            # crashing; the short sleep avoids a tight loop if the device is
            # genuinely gone rather than just transiently busy.
            print(f"Wake-word listening failed ({exc!r}); retrying in 2s.", file=sys.stderr, flush=True)
            time.sleep(2)
            continue

        if time.monotonic() - last_conversation_end < CONTINUATION_WINDOW_SECONDS:
            print("Continuing previous conversation's context", flush=True)
            initial_history = last_history
        else:
            initial_history = None

        last_history = _handle_conversation(
            in_device, out_device, model, wake_word_language, initial_history,
            conversation_language=wake_word_language[triggered_key],
        )
        last_conversation_end = time.monotonic()
        last_trigger = time.monotonic()  # restart cooldown from when we resume listening


if __name__ == "__main__":
    main()
