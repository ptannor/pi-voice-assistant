import threading
import time
from . import spotify


_active_timer_thread = None
_stop_event = threading.Event()
# True from the moment a timer's end-of-timer track starts until the wake
# word acknowledges it -- lets wake_word_daemon.py tell "an alarm the user
# just dismissed by saying the wake word" apart from "regular music paused
# mid-conversation," which should resume afterward while an alarm shouldn't
# (see is_alarm_ringing/acknowledge_alarm below).
_alarm_ringing = False

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
            global _alarm_ringing
            _alarm_ringing = True
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
    global _active_timer_thread, _stop_event, _alarm_ringing
    stopped_music = False
    try:
        spotify.stop()
        stopped_music = True
    except Exception:
        pass
    _alarm_ringing = False

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
    (as opposed to music the user started themselves) -- see
    wake_word_daemon.py's pause/resume-on-wake-word handling.
    """
    return _alarm_ringing


def acknowledge_alarm() -> None:
    """Call once the wake word has paused a ringing alarm -- marks it as
    dismissed so it's never auto-resumed afterward like regular music would be.
    """
    global _alarm_ringing
    _alarm_ringing = False
