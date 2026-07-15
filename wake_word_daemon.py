#!/usr/bin/env python3
"""Wake word daemon: listens for "Alexa", then records a question, sends it
through Claude, and speaks the reply back -- in whichever of English/Hebrew
the user actually spoke.

Uses openWakeWord's free, fully open-source pretrained "alexa" model (no
account, no API key, no signup) as a stand-in for the eventual custom-trained
"Menachem Mendel" / "Mendy" wake words.
"""
from __future__ import annotations

import json
import queue
import sys
import tempfile
import time
import wave
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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

from audio_check.config import DEFAULT_CONFIG
from audio_check.devices import Device, find_input_device, find_output_device
from audio_check.errors import AudioCheckError, PlaybackFailed, RecordingFailed
from audio_check.player import play_wav, play_wav_async
from audio_check.recorder import record_until_silence
from brain.config import WAKE_WORD_MODEL_PATH
from brain.llm import BrainError, ask
from brain.respond import speak_reply, speak_reply_chunks
from brain.stt import TranscriptionError, transcribe

ACK_WAV = Path(__file__).parent / "assets" / "chime.wav"
GOODBYE_WAV = Path(__file__).parent / "assets" / "goodbye_chime.wav"
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
WAKE_WORD = "alexa"  # pretrained fallback -- see _load_wake_word_model below
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80ms at 16kHz -- openWakeWord's recommended chunk size
DETECTION_THRESHOLD = 0.6  # Default threshold when music is not playing, to prevent false triggers
COOLDOWN_SECONDS = 2.0  # ignore re-triggers right as we resume listening
# How long to wait for the user to start talking before giving up.
INITIAL_QUERY_TIMEOUT = 4.0
FOLLOW_UP_TIMEOUT = 3.5  # long enough for a real follow-up, short enough to limit exposure to ambient noise
MAX_FOLLOW_UP_TURNS = 5  # safety cap -- require a fresh "Alexa" after a while regardless
# How long after a conversation ends a fresh "Alexa" is still treated as a
# continuation of it (same history/session_language) rather than starting
# blank. Confirmed needed: a closed-answer reply (not ending in "?") ends the
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


def _load_wake_word_model() -> tuple[Model, str]:
    """Returns (model, wake_word_key) -- wake_word_key is whatever key that
    model's `.predict()` output uses for this wake word, which is only ever
    `WAKE_WORD` ("alexa") for the pretrained model; a custom model's key is
    derived from its own filename instead.

    WAKE_WORD_MODEL_PATH (see brain/config.py) points at a custom-trained
    model (e.g. "Mendy") -- training one requires openWakeWord's own Colab
    notebook (30,000+ hours of negative audio and a multi-framework
    torch+tensorflow training stack make local training impractical here;
    see the README's Wake word section for the actual recipe). Until you've
    trained and pointed at one, this always falls back to the pretrained
    "alexa" model.
    """
    if WAKE_WORD_MODEL_PATH:
        wake_word_key = Path(WAKE_WORD_MODEL_PATH).stem
        model = Model(wakeword_models=[WAKE_WORD_MODEL_PATH], inference_framework="onnx")
        return model, wake_word_key

    openwakeword.utils.download_models(model_names=[WAKE_WORD])
    # Force onnx: the tflite_runtime wheel available on some platforms (e.g. the
    # Pi's aarch64 build) is compiled against NumPy 1.x and breaks under NumPy 2.x
    # ("_ARRAY_API not found"). onnxruntime works correctly on both dev machine and Pi.
    model = Model(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    return model, WAKE_WORD


def _listen_for_wake_word(model: Model, wake_word_key: str, in_device: Device, last_trigger: float) -> float:
    """Block until the wake word is detected; return the new last_trigger time.

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
            prediction = model.predict(pcm)
            score = prediction.get(wake_word_key, 0.0)
            now = time.monotonic()
            current_threshold = 0.35 if spotify_is_playing else DETECTION_THRESHOLD
            if score > current_threshold and (now - last_trigger) > COOLDOWN_SECONDS:
                print(f"Wake word detected: {wake_word_key} (score={score:.2f})", flush=True)
                return now


def _play_wav_with_barge_in(
    filepath: Path,
    in_device: Device,
    out_device: Device,
    model,
    wake_word_key,
) -> bool:
    """Plays a WAV file on out_device while listening to in_device for the wake word.
    Returns True if a barge-in wake word was detected, False otherwise.
    """
    from audio_check.player import _load_wav
    try:
        target_sr = int(out_device.default_samplerate)
        audio, sample_rate = _load_wav(filepath, target_sr=target_sr)
    except Exception as exc:
        print(f"Error loading WAV for playback: {exc}", file=sys.stderr)
        return False

    # Reset the stateful wake word model's hidden states so it forgets the previous trigger
    model.reset()

    # Start playback asynchronously with higher latency to prevent CPU/GIL starvation static noise
    sd.play(audio, samplerate=sample_rate, device=out_device.index, latency='high')
    duration = len(audio) / sample_rate
    start_time = time.monotonic()

    audio_queue: queue.Queue = queue.Queue()
    def callback(indata, frames, time_info, status):
        audio_queue.put(indata[:, 0].copy())

    barge_in = False
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
            try:
                pcm = audio_queue.get(timeout=0.1)
                prediction = model.predict(pcm)
                score = prediction.get(wake_word_key, 0.0)
                # Lower threshold (0.35) during active playback to make it easier to
                # interrupt the assistant's own voice feedback from the speakers.
                if score > 0.35:
                    print(f"Barge-in detected (score={score:.2f})! Interrupting playback.", flush=True)
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
    wake_word_key,
    initial_history: list[dict] | None = None,
) -> list[dict] | None:
    """Returns `history` as it stood when the conversation ended, so `main()`
    can offer it to the *next* call as a continuation if a fresh "Alexa"
    comes in soon enough (see CONTINUATION_WINDOW_SECONDS) -- otherwise this
    always starts blank.
    """
    # Pause Spotify music immediately when conversation starts so the microphone
    # can hear the user's voice clearly.
    was_playing = False
    # Distinguishes "a timer's end-of-timer track, dismissed by the wake word
    # itself" from "regular music paused mid-conversation" -- the former
    # should never come back once acknowledged; the latter should resume
    # exactly as before. See brain/timer.py's is_alarm_ringing/acknowledge_alarm.
    was_alarm = False
    stop_called_in_session = False
    try:
        from brain import spotify, timer
        if spotify.is_playing():
            was_playing = True
            was_alarm = timer.is_alarm_ringing()
            spotify.stop()
            if was_alarm:
                timer.acknowledge_alarm()
    except Exception:
        pass

    history = initial_history
    timeout = INITIAL_QUERY_TIMEOUT
    turns = 0
    # Locked to whichever language the first turn of *this* activation
    # detects -- every later turn within the same activation forces that
    # same language directly (see brain/stt.py's `forced_language`) instead
    # of re-running the dual-language detection each time. Always starts
    # fresh at None even when `initial_history` is continuing a prior
    # conversation's topic -- confirmed necessary: reusing the *previous*
    # activation's language here force-fed the wrong language into Whisper
    # when the user switched languages between activations, making it seem
    # like the assistant "got stuck" on Hebrew.
    session_language: str | None = None
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

            # Fire immediately, not just on tool-use turns -- the median
            # transcribe+ask+first_audio gap is ~3.4s (p90 ~6.5s, see
            # logs/latency.jsonl) even with no tool call, which otherwise
            # feels like dead air. If a tool call *does* happen, on_tool_call
            # below plays a second one partway through the longer wait.
            play_wav_async(THINKING_WAV, out_device)

            stt_mode = "forced" if session_language else "dual"
            text, language = transcribe(query_wav, forced_language=session_language)
            session_language = language
            t2 = time.monotonic()
            print(f"Heard ({language}): {text}", flush=True)
            if not text:
                break

            reply, history, ask_timeline = ask(
                text, language, history,
                on_tool_call=lambda: play_wav_async(THINKING_WAV, out_device),
            )
            t3 = time.monotonic()
            print(f"Claude: {reply}", flush=True)

            # Check if user explicitly asked to stop, or if a stop-related tool was executed
            lower_text = (text or "").lower()
            if any(w in lower_text for w in ["עצור", "עצרי", "סטופ", "stop"]) or any(
                "stop_music" in stage or "cancel_timer" in stage for stage, _ in ask_timeline
            ):
                stop_called_in_session = True

            # Synthesize reply to WAV chunks
            chunks, t_first_audio = speak_reply_chunks(reply)

            # Play each chunk with barge-in
            barge_in = False
            for wav in chunks:
                barge_in = _play_wav_with_barge_in(wav, in_device, out_device, model, wake_word_key)
                wav.unlink(missing_ok=True)
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

            if barge_in:
                timeout = INITIAL_QUERY_TIMEOUT
                continue

            if _said_closing_phrase(text, language):
                break  # explicit "thanks"/"bye" etc. -- end right away

            # Keep listening only when Claude's own reply is a genuine question
            if not reply.rstrip().endswith("?"):
                break
        except (TranscriptionError, BrainError, RecordingFailed, PlaybackFailed) as exc:
            print(f"Conversation turn failed: {exc}", file=sys.stderr, flush=True)
            break
        except Exception as exc:
            print(f"Unexpected error handling conversation: {exc!r}", file=sys.stderr, flush=True)
            break
        finally:
            query_wav.unlink(missing_ok=True)
        timeout = FOLLOW_UP_TIMEOUT

    # Every exit from the loop above (silence timeout, closing phrase, turn
    # cap, or an error) falls through to here -- one clear signal that it's
    # stopped listening, distinct from the ascending "now listening" chime.
    try:
        play_wav(GOODBYE_WAV, out_device)
    except PlaybackFailed as exc:
        print(f"Goodbye chime failed: {exc}", file=sys.stderr, flush=True)

    # If music was playing before and we didn't explicitly request to stop it, resume playback --
    # but never for a timer alarm the wake word already dismissed (see was_alarm above).
    if was_playing and not was_alarm and not stop_called_in_session:
        try:
            from brain import spotify
            spotify.resume()
        except Exception:
            pass

    return history


def main() -> None:
    cfg = DEFAULT_CONFIG
    try:
        in_device = find_input_device(cfg.input_name_hint)
        out_device = find_output_device(cfg.output_name_hint)
    except AudioCheckError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    model, wake_word_key = _load_wake_word_model()

    print(
        f"Listening for '{wake_word_key}' on '{in_device.name}' (index {in_device.index})...",
        flush=True,
    )
    print(f"Responses play on '{out_device.name}' (index {out_device.index})", flush=True)

    last_trigger = 0.0
    last_conversation_end = float("-inf")
    last_history: list[dict] | None = None

    # Start the Spotify background poll thread to dynamically adjust wake word sensitivity
    threading.Thread(target=_poll_spotify_status, daemon=True).start()

    while True:
        last_trigger = _listen_for_wake_word(model, wake_word_key, in_device, last_trigger)

        if time.monotonic() - last_conversation_end < CONTINUATION_WINDOW_SECONDS:
            print("Continuing previous conversation's context", flush=True)
            initial_history = last_history
        else:
            initial_history = None

        last_history = _handle_conversation(in_device, out_device, model, wake_word_key, initial_history)
        last_conversation_end = time.monotonic()
        last_trigger = time.monotonic()  # restart cooldown from when we resume listening


if __name__ == "__main__":
    main()
