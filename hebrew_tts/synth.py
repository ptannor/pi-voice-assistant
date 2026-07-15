"""Hebrew speech synthesis via Microsoft Edge TTS (free, no account/API key).

Edge TTS only outputs MP3; our playback pipeline (audio_check/player.py) is
WAV-only (stdlib `wave` module), so this converts via `ffmpeg`, which must be
installed wherever this runs (`brew install ffmpeg` / `sudo apt install
ffmpeg`).
"""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

import edge_tts

DEFAULT_VOICE = "he-IL-AvriNeural"
# +20% confirmed by listening: default pacing left unnaturally long pauses
# between short clauses/questions.
DEFAULT_RATE = "+20%"
DEFAULT_PITCH = "+0Hz"


class SynthesisError(Exception):
    pass


async def _synthesize_mp3(text: str, mp3_path: Path, voice: str, rate: str, pitch: str) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(str(mp3_path))


def synthesize_to_wav(
    text: str,
    output_path: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
) -> None:
    """Synthesize Hebrew text to a WAV file at output_path.

    Takes text as-is -- callers decide whether to pass plain text or
    nakdan.vocalize()'d text, per-phrase (see pronunciation.py for why that
    decision isn't automatic).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        mp3_path = Path(tmp_dir) / "speech.mp3"
        try:
            asyncio.run(_synthesize_mp3(text, mp3_path, voice, rate, pitch))
        except Exception as exc:  # edge_tts raises its own exception types
            raise SynthesisError(f"Edge TTS synthesis failed: {exc}") from exc

        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path), "-codec:a", "pcm_s16le", "-ar", "16000", "-ac", "1", str(output_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SynthesisError(f"ffmpeg conversion failed: {result.stderr}")
