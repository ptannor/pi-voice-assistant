#!/usr/bin/env python3
"""Telegram bot daemon: lets the household edit Mendy's calendar (or talk to
the same brain generally) by messaging a Telegram bot, instead of speaking to
the Pi -- the third of the three calendar-editing paths requested alongside
the Google Calendar app and voice (see brain/gcal.py, brain/reminders.py).

Long-polling (python-telegram-bot's Application.run_polling()) -- no inbound
port, no public webhook URL, works fine behind home NAT, no Meta-style app
review. Runs as its own systemd unit (systemd/pi-telegram-bot.service),
independent of wake_word_daemon.py's process -- brain/audio_focus.py's
single-process ALERT channel doesn't apply here since this daemon never
touches audio.
"""
from __future__ import annotations

import sys

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from brain.config import TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
from brain.language import detect_language
from brain.llm import BrainError, ask

# Per-chat conversation history. Telegram has no "conversation ended" signal
# like the voice daemon's silence timeout, so history just keeps growing per
# chat -- capped here (oldest turns dropped) to bound the prompt's size, not
# because the conversation itself ever explicitly ends.
_MAX_HISTORY_MESSAGES = 20
_history: dict[int, list[dict]] = {}


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or not message.text:
        return
    chat_id = message.chat_id

    # Checked on every message, not just once -- an allowlist stored in
    # .pi-config can change between messages, and this is the only thing
    # standing between a stranger who finds the bot's username and full tool
    # access (calendar writes, web search, memory).
    if str(chat_id) not in TELEGRAM_ALLOWED_CHAT_IDS:
        print(f"Refusing message from non-allowlisted chat {chat_id}", flush=True)
        await message.reply_text("This bot isn't set up for this chat.")
        return

    text = message.text.strip()
    if not text:
        return

    # No acoustic hint available for typed text -- detect_language() falls
    # back to the Hebrew-Unicode text check, which is exactly what it's for
    # (see brain/language.py's module docstring).
    language = detect_language(text)
    history = _history.get(chat_id)

    try:
        reply, history, _timeline = ask(text, language, history)
    except BrainError as exc:
        print(f"Telegram turn failed: {exc}", file=sys.stderr, flush=True)
        await message.reply_text("Something went wrong -- try again in a moment.")
        return

    _history[chat_id] = history[-_MAX_HISTORY_MESSAGES:]
    if reply:
        await message.reply_text(reply)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram bot isn't configured yet -- see README for the one-time .env setup.", file=sys.stderr)
        sys.exit(1)
    if not TELEGRAM_ALLOWED_CHAT_IDS:
        print("Warning: TELEGRAM_ALLOWED_CHAT_IDS is empty in .pi-config -- every message will be refused.", flush=True)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    print("Telegram bot listening...", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
