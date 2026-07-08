#!/usr/bin/env python3
"""Wake word daemon: listens for "Alexa", then records a question, sends it
through Claude, and speaks the reply back -- in whichever of English/Hebrew
the user actually spoke.

Uses openWakeWord's free, fully open-source pretrained "alexa" model (no
account, no API key, no signup) as a stand-in for the eventual custom-trained
"Menachem Mendel" / "Mendy" wake words.
"""
from __future__ import annotations

import queue
import sys
import tempfile
import time
from pathlib import Path

import openwakeword
import sounddevice as sd
from openwakeword.model import Model

from audio_check.config import DEFAULT_CONFIG
from audio_check.devices import Device, find_input_device, find_output_device
from audio_check.errors import AudioCheckError, PlaybackFailed, RecordingFailed
from audio_check.player import play_wav
from audio_check.recorder import record_until_silence
from brain.config import WAKE_WORD_MODEL_PATH
from brain.llm import BrainError, ask
from brain.respond import synthesize_reply
from brain.stt import TranscriptionError, transcribe

ACK_WAV = Path(__file__).parent / "assets" / "chime.wav"
GOODBYE_WAV = Path(__file__).parent / "assets" / "goodbye_chime.wav"
WAKE_WORD = "alexa"  # pretrained fallback -- see _load_wake_word_model below
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80ms at 16kHz -- openWakeWord's recommended chunk size
DETECTION_THRESHOLD = 0.5
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

    with sd.InputStream(
        device=in_device.index,
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="int16",
        blocksize=CHUNK_SAMPLES,
        callback=callback,
    ):
        while True:
            pcm = audio_queue.get()
            prediction = model.predict(pcm)
            score = prediction.get(wake_word_key, 0.0)
            now = time.monotonic()
            if score > DETECTION_THRESHOLD and (now - last_trigger) > COOLDOWN_SECONDS:
                print(f"Wake word detected: {wake_word_key} (score={score:.2f})", flush=True)
                return now


def _handle_conversation(
    in_device: Device,
    out_device: Device,
    initial_history: list[dict] | None = None,
    initial_session_language: str | None = None,
) -> tuple[list[dict] | None, str | None]:
    """Returns (history, session_language) as they stood when the
    conversation ended, so `main()` can offer them to the *next* call as a
    continuation if a fresh "Alexa" comes in soon enough (see
    CONTINUATION_WINDOW_SECONDS) -- otherwise this always starts blank.
    """
    history = initial_history
    timeout = INITIAL_QUERY_TIMEOUT
    turns = 0
    # Locked to whichever language the first turn detects -- every later turn
    # forces that same language directly (see brain/stt.py's `forced_language`)
    # instead of re-running the dual-language detection each time. Cheaper,
    # and gets the full forced-language accuracy benefit for the whole
    # conversation without needing a second wake word per language.
    session_language: str | None = initial_session_language
    while turns < MAX_FOLLOW_UP_TURNS:
        turns += 1
        query_wav = Path(tempfile.mktemp(suffix=".wav"))
        reply_wav: Path | None = None
        try:
            # Play the chime and WAIT for it to finish before recording starts --
            # never record while our own audio is playing, or the mic picks up
            # our own chime/reply as if it were user speech (confirmed: this
            # was corrupting transcriptions and confusing the silence-detector
            # into cutting turns short). No AEC hardware on this mic, so strict
            # turn-taking (we talk, then we listen) is the only reliable option.
            # (A wake-word-only "barge-in" during playback was tried and
            # reverted -- it self-triggered on bleed from our own reply audio,
            # since there's no acoustic echo cancellation to tell our own
            # voice apart from the user's. Real barge-in needs either AEC
            # (software, e.g. WebRTC's AEC3, or an AEC-capable mic/speaker) to
            # cancel the known reply signal out of the mic input before
            # running wake-word detection on what's left.)
            play_wav(ACK_WAV, out_device)

            t0 = time.monotonic()
            recorded = record_until_silence(
                in_device, query_wav, SAMPLE_RATE, 1, initial_timeout=timeout
            )
            t1 = time.monotonic()
            if recorded is None:
                break  # nothing said -- end the conversation, back to wake-word listening

            text, language = transcribe(query_wav, forced_language=session_language)
            session_language = language
            t2 = time.monotonic()
            print(f"Heard ({language}): {text}", flush=True)
            if not text:
                break

            reply, history = ask(text, language, history)
            t3 = time.monotonic()
            print(f"Claude: {reply}", flush=True)

            reply_wav = synthesize_reply(reply)
            t4 = time.monotonic()
            print(
                f"[timing] record={t1 - t0:.1f}s transcribe={t2 - t1:.1f}s "
                f"ask={t3 - t2:.1f}s synthesize={t4 - t3:.1f}s",
                flush=True,
            )

            play_wav(reply_wav, out_device)

            if _said_closing_phrase(text, language):
                break  # explicit "thanks"/"bye" etc. -- end right away

            # Keep listening (silently -- no spoken "anything else?" prompt,
            # that caused an awkward double-ask with whatever Claude itself
            # said) only when Claude's own reply is a genuine question,
            # meaning the thread is actually still open. A plain, closed
            # answer ("Of course I do!") has nothing left open, so end there
            # instead of leaving the mic on for no reason.
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
            if reply_wav is not None:
                reply_wav.unlink(missing_ok=True)
        timeout = FOLLOW_UP_TIMEOUT

    # Every exit from the loop above (silence timeout, closing phrase, turn
    # cap, or an error) falls through to here -- one clear signal that it's
    # stopped listening, distinct from the ascending "now listening" chime.
    try:
        play_wav(GOODBYE_WAV, out_device)
    except PlaybackFailed as exc:
        print(f"Goodbye chime failed: {exc}", file=sys.stderr, flush=True)

    return history, session_language


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
    last_session_language: str | None = None
    while True:
        last_trigger = _listen_for_wake_word(model, wake_word_key, in_device, last_trigger)

        if time.monotonic() - last_conversation_end < CONTINUATION_WINDOW_SECONDS:
            print("Continuing previous conversation's context", flush=True)
            initial_history, initial_session_language = last_history, last_session_language
        else:
            initial_history, initial_session_language = None, None

        last_history, last_session_language = _handle_conversation(
            in_device, out_device, initial_history, initial_session_language
        )
        last_conversation_end = time.monotonic()
        last_trigger = time.monotonic()  # restart cooldown from when we resume listening


if __name__ == "__main__":
    main()
