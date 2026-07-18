"""Best-effort outbound Telegram push, shared by brain/reminders.py's
spoken-reminder companion messages and brain/classify.py's uncertain-
classification questions -- see telegram_bot_daemon.py for the two-way
conversational side.
"""
from __future__ import annotations

import urllib.parse
import urllib.request

from .config import TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN


def push(text: str) -> None:
    """A Telegram outage (or the bot not being configured at all) must never
    block whatever's calling this, hence the broad except -- always a
    nice-to-have companion, never the primary delivery mechanism."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALLOWED_CHAT_IDS:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_ALLOWED_CHAT_IDS:
        try:
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
            urllib.request.urlopen(url, data=data, timeout=5)
        except Exception:
            pass
