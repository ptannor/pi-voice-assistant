import threading
import time
from . import spotify
from .audio_focus import Channel, manager as focus


_active_timer_thread = None
_stop_event = threading.Event()

_REGULAR_TRACK = "spotify:track:70C4NyhjD5OZUMzvWZ3njJ"  # Piano Man, Billy Joel
# Requested (one of the kids, in Hebrew): timers longer than 15 minutes get
# Hedwig's Theme instead of the regular track.
_LONG_TIMER_THRESHOLD_SECONDS = 15 * 60
_LONG_TIMER_QUERY = "Hedwig's Theme Harry Potter"


def set_timer(duration_seconds: int) -> str:
    """Starts a background thread that sleeps for `duration_seconds` and then
    starts playing an end-of-timer track on Spotify -- Hedwig's Theme for
    timers over 15 minutes, Piano Man otherwise.
    """
    global _active_timer_thread, _stop_event

    if duration_seconds <= 0:
        return "שגיאה: משך זמן הטיימר חייב להיות גדול מאפס."

    # If a timer is already running, cancel it first
    cancel_timer()

    _stop_event.clear()

    def timer_target():
        # Sleep in small 1-second steps so we can cancel it quickly if requested
        elapsed = 0
        while elapsed < duration_seconds and not _stop_event.is_set():
            time.sleep(1)
            elapsed += 1

        if not _stop_event.is_set():
            # Grab the ALERT channel first: this snapshots+pauses any music the
            # user had playing (so it can resume after the alarm is dismissed)
            # and preempts an in-progress spoken reply, before the alarm track
            # takes over the Spotify device.
            focus.acquire(Channel.ALERT)
            try:
                if duration_seconds > _LONG_TIMER_THRESHOLD_SECONDS:
                    print("Timer finished! Playing Hedwig's Theme (long timer).", flush=True)
                    spotify.play(_LONG_TIMER_QUERY)
                else:
                    print("Timer finished! Playing Piano Man Billy Joel (exact track).", flush=True)
                    spotify.play(_REGULAR_TRACK)
            except Exception as e:
                print(f"Failed to play timer-end track: {e}", flush=True)

    _active_timer_thread = threading.Thread(target=timer_target, daemon=True)
    _active_timer_thread.start()
    return f"הטיימר הוגדר בהצלחה ל-{duration_seconds} שניות."


def cancel_timer() -> str:
    """Cancels the currently running background timer and stops Spotify music."""
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
        return "הטיימר בוטל והשיר נעצר."

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
