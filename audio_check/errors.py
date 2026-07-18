class AudioCheckError(Exception):
    """Base error for all audio_check failures. Callers can catch this alone."""


class NoInputDeviceFound(AudioCheckError):
    pass


class NoOutputDeviceFound(AudioCheckError):
    pass


class AudioBackendError(AudioCheckError):
    """Raised when the OS audio backend (ALSA/PulseAudio/PipeWire) itself is misbehaving."""


class RecordingFailed(AudioCheckError):
    pass


class WakeWordInterrupt(AudioCheckError):
    """Raised by an `on_chunk` hook passed to record_until_silence to abort
    the recording early -- e.g. the user said a wake word again mid-recording
    to restart what they were saying. `key` is which wake word fired; `preroll`
    is the raw audio right up to it, handed to the next recording attempt as a
    head start (same role as record_until_silence's own preroll_chunks)."""

    def __init__(self, key: str, preroll: list | None = None) -> None:
        super().__init__(f"Interrupted by wake word: {key}")
        self.key = key
        self.preroll = preroll or []


class PlaybackFailed(AudioCheckError):
    pass
