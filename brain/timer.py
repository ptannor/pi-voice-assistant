import threading
import time
from . import spotify


_active_timer_thread = None
_stop_event = threading.Event()


def set_timer(duration_seconds: int) -> str:
    """Starts a background thread that sleeps for `duration_seconds`
    and then starts playing 'Piano Man' on Spotify.
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
            try:
                # When the timer ends, trigger Spotify to play Piano Man
                print("Timer finished! Playing Piano Man.", flush=True)
                spotify.play("Piano Man")
            except Exception as e:
                print(f"Failed to play Piano Man at timer end: {e}", flush=True)

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

    if _active_timer_thread and _active_timer_thread.is_alive():
        _stop_event.set()
        _active_timer_thread.join(timeout=1.0)
        return "הטיימר בוטל והשיר נעצר."

    if stopped_music:
        return "השיר נעצר בהצלחה."
    return "אין טיימר פעיל או שיר לביטול."
