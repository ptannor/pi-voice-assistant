import threading
import time
from pathlib import Path

from audio_check.player import play_wav

from . import spotify
from .audio_focus import Channel, manager as focus
from .config import TIMER_SOUND_PATH
from .respond import speak_reply


_active_timer_thread = None
_stop_event = threading.Event()


def _default_label(duration_seconds: int, language: str) -> str:
    """A complete, natural phrase for a timer with no specific purpose given
    -- e.g. "15 minute timer" / "טיימר ל-15 דקות" -- so it always announces
    something concrete ("your 15 minute timer is done") rather than a bare
    "your timer is done" that doesn't distinguish which one, if more than
    one had been set today."""
    minutes = duration_seconds / 60
    if minutes >= 1:
        amount = int(minutes) if minutes == int(minutes) else round(minutes, 1)
        return f"טיימר ל-{amount} דקות" if language == "he" else f"{amount} minute timer"
    return f"טיימר ל-{duration_seconds} שניות" if language == "he" else f"{duration_seconds} second timer"


def _announcement(label: str, language: str) -> str:
    return f"{label} הסתיים" if language == "he" else f"Your {label} is done."


def set_timer(duration_seconds: int, out_device=None, label: str | None = None, language: str = "he") -> str:
    """Starts a background thread that sleeps for `duration_seconds`, speaks
    an announcement once (see _announcement/_default_label -- confirmed a
    real gap: a bare looping sound with no accompanying text gave no way to
    tell which timer finished, or that it was a timer at all rather than a
    reminder/alarm), then loops TIMER_SOUND_PATH (see brain/config.py) until
    cancelled -- one sound for every timer regardless of duration, replacing
    the old Piano Man/Hedwig's Theme Spotify tracks by household request.
    `out_device` is the audio_check.devices.Device to play on; if it's None
    (e.g. a caller with no speaker, like the Telegram bot) the timer still
    runs but ends silently, same tolerance the rest of this codebase gives a
    missing audio cue. `label`, if given, should already be a complete,
    natural phrase for what the timer is for (see the set_timer tool
    schemas in brain/tools.py); otherwise a duration-based default is used.
    """
    global _active_timer_thread, _stop_event

    # If a timer is already running, cancel it first
    cancel_timer()

    _stop_event.clear()
    label = label or _default_label(duration_seconds, language)

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
        if out_device is None:
            print(f"Timer finished! ({label}, no output device -- silent)", flush=True)
            return
        print(f"Timer finished! Announcing '{label}' then looping timer sound until cancelled.", flush=True)
        try:
            speak_reply(_announcement(label, language), out_device)
        except Exception as e:
            print(f"Failed to speak timer announcement: {e}", flush=True)
        if not TIMER_SOUND_PATH:
            return
        while not _stop_event.is_set():
            try:
                play_wav(Path(TIMER_SOUND_PATH), out_device)
            except Exception as e:
                print(f"Failed to play timer sound: {e}", flush=True)
                break

    _active_timer_thread = threading.Thread(target=timer_target, daemon=True)
    _active_timer_thread.start()
    return f"הטיימר הוגדר בהצלחה ל-{duration_seconds} שניות."


def _stop_alarm_thread() -> bool:
    """Signals the looping alarm thread to stop and waits (briefly) for it to
    actually exit. Returns whether there was one running. A bare _stop_event.set()
    without joining isn't enough -- the loop can still be mid-play_wav() on
    the alarm sound, and the caller (dismiss_ringing_alarm, just below) is
    about to want the same output device itself immediately after; joining
    first ensures that play_wav() call has actually returned before anything
    else touches the device, rather than relying on audio_check.player's
    playback lock alone to serialize them."""
    if _active_timer_thread and _active_timer_thread.is_alive():
        _stop_event.set()
        _active_timer_thread.join(timeout=2.0)
        return True
    return False


def dismiss_ringing_alarm() -> None:
    """Stops the looping alarm-sound thread immediately, without
    cancel_timer()'s other side effects (stopping Spotify, releasing the
    ALERT channel -- the caller, brain/audio_focus.py's DIALOG acquire,
    already handles releasing ALERT itself as part of waking up on a ringing
    alarm).

    Confirmed necessary: waking up on a ringing alarm used to only update
    the focus manager's bookkeeping (marking ALERT released), while the
    alarm loop thread itself kept calling play_wav() in a tight loop,
    oblivious to that -- it only ever checks _stop_event, which nothing set
    until cancel_timer() ran later, well after the conversation had already
    started. That left the alarm thread and the wake-word ack chime's own
    play_wav() call contending for the same output device at the same time,
    which hung indefinitely (see audio_check/player.py's playback lock for
    the other half of this fix).
    """
    _stop_alarm_thread()


def cancel_timer() -> str:
    """Cancels the currently running background timer and stops Spotify music
    (in case regular music, not the timer sound, is what's playing)."""
    stopped_music = False
    try:
        spotify.stop()
        stopped_music = True
    except Exception:
        pass
    # Release the ALERT channel: dismisses a ringing alarm. Any music that was
    # playing before the alarm resumes when the current speaking turn ends.
    focus.release(Channel.ALERT)

    if _stop_alarm_thread():
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
