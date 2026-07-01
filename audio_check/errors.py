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


class PlaybackFailed(AudioCheckError):
    pass
