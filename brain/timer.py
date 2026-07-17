import threading
import time
from pathlib import Path

from audio_check.player import play_wav

from . import spotify
from .audio_focus import Channel, manager as focus
from .config import TIMER_SOUND_PATH


_active_timer_thread = None
_stop_event = threading.Event()


def set_timer(duration_seconds: int, out_device=None) -> str:
    """Starts a background thread that sleeps for `duration_seconds` and then
    loops TIMER_SOUND_PATH (see brain/config.py) until cancelled -- one sound
    for every timer regardless of duration, replacing the old Piano
    Man/Hedwig's Theme Spotify tracks by household request. `out_device` is
    the audio_check.devices.Device to play on; if it's None (e.g. a caller
    with no speaker, like the Telegram bot) the timer still runs but ends
    silently, same tolerance the rest of this codebase gives a missing
    audio cue.
    """
    global _active_timer_thread, _stop_event

    # If a timer is already running, cancel it first
    cancel_timer()

    _stop_event.clear()

    def timer_target():
        # Sleep in small 1-second steps so we can cancel it quickly if requested
        elapsed = 0
        while elapsed < duration_seconds and not _stop_event.is_set():
            time.sleep(1)
            elapsed += 1

        if _stop_event.is_set():
            return

        # Grab the ALERT channel first: this snapshots+pauses any music the
        # user had playing (so it can resume after the alarm is dismissed)
        # and preempts an in-progress spoken reply, before the alarm sound
        # starts.
        focus.acquire(Channel.ALERT)
        if out_device is None or not TIMER_SOUND_PATH:
            print("Timer finished! (no output device or TIMER_SOUND_PATH configured -- silent)", flush=True)
            return
        print("Timer finished! Looping timer sound until cancelled.", flush=True)
        while not _stop_event.is_set():
            try:
                play_wav(Path(TIMER_SOUND_PATH), out_device)
            except Exception as e:
                print(f"Failed to play timer sound: {e}", flush=True)
                break

    _active_timer_thread = threading.Thread(target=timer_target, daemon=True)
    _active_timer_thread.start()
    return f"הטיימר הוגדר בהצלחה ל-{duration_seconds} שניות."


def cancel_timer() -> str:
    """Cancels the currently running background timer and stops Spotify music
    (in case regular music, not the timer sound, is what's playing)."""
    global _active_timer_thread, _stop_event
    stopped_music = False
    try:
        spotify.stop()
        stopped_music = True
    except Exception:
        pass
    # Release the ALERT channel: dismisses a ringing alarm. Any music that was
    # playing before the alarm resumes when the current speaking turn ends.
    focus.release(Channel.ALERT)

    if _active_timer_thread and _active_timer_thread.is_alive():
        _stop_event.set()
        _active_timer_thread.join(timeout=1.0)
        return "הטיימר בוטל."

    if stopped_music:
        return "השיר נעצר בהצלחה."
    return "אין טיימר פעיל או שיר לביטול."


def is_timer_active() -> bool:
    """Check if the background timer thread is active and running."""
    global _active_timer_thread
    return _active_timer_thread is not None and _active_timer_thread.is_alive()


def is_alarm_ringing() -> bool:
    """Whether a timer's end-of-timer track is the thing currently playing
    (as opposed to music the user started themselves). Backed by the ALERT
    channel of the shared audio-focus manager (see brain/audio_focus.py).
    """
    return focus.is_active(Channel.ALERT)


def acknowledge_alarm() -> None:
    """Dismiss a ringing alarm -- releases the ALERT channel so it's never
    auto-resumed afterward like regular music would be.
    """
    focus.release(Channel.ALERT)
