"""API keys for the STT/LLM pipeline, loaded from a local `.env` (gitignored).

Personal keys only -- this is a personal public repo, not Check Point work.
Never point ANTHROPIC_API_KEY/GROQ_API_KEY at a corporate gateway or
credential (see the UV_INDEX incident in the README's troubleshooting section
for why that's a hard rule here, not just a suggestion).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Haiku: fast/cheap, appropriate for short spoken Q&A read aloud by TTS.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Groq's hosted Whisper -- chosen over OpenAI's own Whisper API (cheaper,
# faster) and over self-hosting ivrit.ai's Hebrew-tuned models (better Hebrew
# accuracy, but Hebrew-only and requires running your own GPU endpoint --
# not worth the ops overhead for a hobby project that also needs English).
STT_MODEL = "whisper-large-v3-turbo"
