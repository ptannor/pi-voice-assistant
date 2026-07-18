"""A tiny audio-focus manager for this daemon's three audio layers.

Modeled directly on the focus/interruption systems real voice assistants and
mobile OSes use for exactly this problem -- layered audio that must pause, duck,
preempt, and resume correctly:

  * Amazon AVS Focus Management -- a small set of prioritized *channels* where a
    higher, non-mixable channel takes the foreground and backgrounds the ones
    below it, then hands focus back on release.
  * Android AudioManager audio focus -- transient focus loss (pause, resume
    later) vs. permanent loss (stop, do NOT resume); ducking as a separate case.
  * Apple AVAudioSession interruptions -- `.began`/`.ended` with the
    `shouldResume` hint deciding whether a backgrounded layer comes back.

Channels here (highest priority first), matched to this project's actual needs:

  ALERT   -- timer end-of-timer alarm. Highest, non-mixable: preempts everything
             below and *discards* it (the layer below does not come back when
             the alert releases -- except CONTENT, see below).
  DIALOG  -- conversational TTS replies / jokes. Non-mixable w.r.t. CONTENT
             (pauses the music while speaking). Ephemeral: if an ALERT preempts
             it, it is abandoned, never resumed.
  CONTENT -- Spotify music. Lowest, and the only *resumable* layer: when the
             layers above it all release, it resumes exactly where it left off
             (see spotify.capture_playback_state / resume_playback_state), which
             is the AVAudioSession `shouldResume` / Android transient-loss case.

NOTE ON ORDERING: AVS's own order is Dialog > Alerts > Content. This project
inverts the top two (ALERT > DIALOG) on purpose -- the stated requirement is
that a firing timer alarm preempts an in-progress spoken reply, not the other
way around. The *mechanism* (prioritized channels, foreground/background,
non-mixable preempt, resume-vs-discard) is the researched one; only this one
ordering choice is project-specific.

Single process, three channels, one background timer thread -- deliberately not
a general framework. All state is guarded by a single re-entrant lock; Spotify
network calls for the resume fade are done outside the lock so a firing alarm is
never blocked waiting on a resume in progress.
"""
from __future__ import annotations

import threading
from enum import IntEnum


class Channel(IntEnum):
    CONTENT = 1  # Spotify music -- lowest, resumable
    DIALOG = 2   # conversational TTS -- pauses content, ephemeral
    ALERT = 3    # timer alarm -- highest, preempts & discards lower layers


class AudioFocusManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: set[Channel] = set()
        # CONTENT (music) bookkeeping. Snapshot is taken once, on the transition
        # from playing -> backgrounded, and preserved across any number of
        # higher-channel acquire/release cycles until CONTENT actually resumes.
        self._content_backgrounded = False
        self._content_should_resume = False
        self._content_state: dict | None = None
        # Per-DIALOG-session flags.
        self._dialog_preempted = False
        self._dialog_opened_on_alert = False

    def is_active(self, channel: Channel) -> bool:
        with self._lock:
            return channel in self._active

    def alert_active(self) -> bool:
        return self.is_active(Channel.ALERT)

    def dialog_opened_on_alert(self) -> bool:
        """True if the current DIALOG session began while an alarm was ringing
        -- i.e. the user woke up to dismiss the alarm. Lets the daemon read a
        "stop" in that session as "stop the alarm" (music should still resume)
        rather than "stop the music"."""
        with self._lock:
            return self._dialog_opened_on_alert

    def is_preempted(self, channel: Channel) -> bool:
        """True if a strictly higher-priority channel currently holds focus --
        the signal for a DIALOG playback loop to abandon itself when an ALERT
        fires."""
        with self._lock:
            return any(c > channel for c in self._active)

    def acquire(self, channel: Channel) -> None:
        with self._lock:
            if channel == Channel.DIALOG:
                self._dialog_preempted = False
                self._dialog_opened_on_alert = False
                self._background_content_locked()
                if Channel.ALERT in self._active:
                    # Woke up on a ringing alarm: taking DIALOG dismisses it.
                    # Add DIALOG *before* releasing ALERT so the release doesn't
                    # prematurely resume CONTENT (DIALOG still holds foreground).
                    self._dialog_opened_on_alert = True
                    self._active.add(Channel.DIALOG)
                    self._release_locked(Channel.ALERT, resume=False)
                    # Actually stop the timer's looping alarm sound now, not
                    # just the bookkeeping above -- the loop thread only
                    # checks its own stop signal, and otherwise keeps calling
                    # play_wav() obliviously, contending with (and once,
                    # hanging against) the wake-word ack chime's own
                    # play_wav() call on the same output device. See
                    # brain/timer.py's dismiss_ringing_alarm().
                    self._dismiss_ringing_alarm()
                self._active.add(Channel.DIALOG)
                self._pause_spotify()
            elif channel == Channel.ALERT:
                # Snapshot genuine music before the alarm track overwrites it on
                # the shared Connect device. If DIALOG is up, mark it preempted
                # (discarded, not queued). The caller plays the alarm afterward.
                self._background_content_locked()
                if Channel.DIALOG in self._active:
                    self._dialog_preempted = True
                self._active.add(Channel.ALERT)

    def release(self, channel: Channel) -> None:
        with self._lock:
            state = self._release_locked(channel, resume=True)
        if state is not None:
            # Outside the lock: the fade takes ~1.5s and must not block a
            # concurrently-firing alarm's acquire(). abort_check lets the fade
            # bail the instant an alarm grabs the device.
            from . import spotify
            try:
                spotify.resume_playback_state(state, fade=True, abort_check=self.alert_active)
            except Exception:
                pass

    def suppress_resume(self) -> None:
        """Don't restore the pre-conversation snapshot when the speaking
        layer releases -- call this whenever a tool executed *during* the
        current DIALOG/ALERT session has already deliberately changed what's
        playing (explicitly stopped it, skipped/sought within it, or started
        something new). Without this, the snapshot captured at acquire()
        time -- necessarily from *before* any of that happened -- silently
        overwrites the user's own in-conversation change the moment the
        conversation ends: confirmed live, "skip to the next song" moved to
        a new track, then got reverted back to the original song a few
        seconds later when the conversation closed and the stale snapshot
        resumed over it."""
        with self._lock:
            self._content_should_resume = False

    # -- internals (call with the lock held) --------------------------------

    def _release_locked(self, channel: Channel, resume: bool) -> dict | None:
        self._active.discard(channel)
        if channel == Channel.DIALOG:
            self._dialog_opened_on_alert = False
            self._dialog_preempted = False
        # Once no channel above CONTENT remains, CONTENT is no longer
        # backgrounded: either resume it (returning its snapshot for the caller
        # to act on outside the lock) or drop the snapshot if it was explicitly
        # stopped. Clearing the flag in *both* cases is essential -- otherwise a
        # stopped song leaves _content_backgrounded set and silently suppresses
        # the resume of the *next* song that gets backgrounded.
        if self._content_backgrounded and not any(c > Channel.CONTENT for c in self._active):
            state = self._content_state if (resume and self._content_should_resume) else None
            self._content_backgrounded = False
            self._content_should_resume = False
            self._content_state = None
            return state  # caller performs the actual resume outside the lock
        return None

    def _background_content_locked(self) -> None:
        # Snapshot + remember to resume, only when *genuine* music is the
        # audible layer. If an ALERT is already active the audible thing is an
        # alarm, not content; if content is already backgrounded the earlier
        # snapshot (the real song) must be kept, not overwritten.
        if self._content_backgrounded or Channel.ALERT in self._active:
            return
        from . import spotify
        try:
            if spotify.is_playing():
                self._content_state = spotify.capture_playback_state()
                self._content_backgrounded = True
                self._content_should_resume = True
        except Exception:
            pass

    def _pause_spotify(self) -> None:
        from . import spotify
        try:
            spotify.stop()
        except Exception:
            pass

    def _dismiss_ringing_alarm(self) -> None:
        from . import timer
        try:
            timer.dismiss_ringing_alarm()
        except Exception:
            pass


manager = AudioFocusManager()
