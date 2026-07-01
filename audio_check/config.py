from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 44100
    channels: int = 1
    duration_seconds: float = 5.0
    dtype: str = "int16"

    # Substring match (case-insensitive) used to auto-pick devices by name.
    # Leave as None to fall back to the system default device.
    input_name_hint: str | None = "QuadCast"
    output_name_hint: str | None = None

    output_dir: Path = Path("recordings")
    test_filename: str = "mic_test.wav"

    @property
    def test_filepath(self) -> Path:
        return self.output_dir / self.test_filename


DEFAULT_CONFIG = AudioConfig()
