"""Haiku-based categorization of Mendy calendar reminders into
critical/morning/regular, plus the household-facing disambiguation flow for
when it isn't confident.

The three real categories drive different alarm behavior (see
brain/reminders.py's _sound_for/_fire):
  - "morning": the daily wake-up alarm -- gets the recorded wake-up sound
    instead of the generic reminder chime.
  - "critical": high-consequence and time-sensitive if missed (flights,
    medication, cancelling a paid subscription/trial) -- nudges
    periodically, in conversation and proactively, until explicitly
    acknowledged, instead of firing once like a normal reminder.
  - "regular": everything else -- the default one-shot spoken reminder.

A fourth, internal-only state, "uncertain", means the classifier itself
wasn't confident. The event is still created (behaving like "regular" in the
meantime -- the safe default) but a disambiguation question is queued: a
Telegram message goes out immediately (see telegram_bot_daemon.py's bare-word
reply handling), and if nobody answers within UNCERTAIN_ESCALATE_AFTER_HOURS
it's also raised as a spoken question during the next live conversation (see
brain/llm.py's _uncertain_classification_prompt_line).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL

CATEGORIES = ("critical", "morning", "regular")
UNCERTAIN = "uncertain"

# How long a Telegram-only disambiguation question waits for a reply before
# also becoming a spoken question -- long enough that a reasonable reply
# window has passed, short enough that a genuinely time-sensitive item (the
# whole reason "critical" exists) isn't left unclassified for a whole day.
UNCERTAIN_ESCALATE_AFTER_HOURS = 4

_STATE_PATH = Path(__file__).parent.parent / "logs" / "pending_classifications.json"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


_SYSTEM = """Classify a household calendar reminder into exactly one word:
critical, morning, or regular.

- "morning": a daily wake-up alarm (e.g. "Morning Alarm", "Wake up", "בוקר טוב").
- "critical": high-consequence and time-sensitive if missed -- a flight or
  other travel departure, medication, cancelling a paid subscription/trial
  before it renews, a bill or payment due, an official/legal deadline.
- "regular": any other ordinary reminder or appointment.

If the title and notes genuinely don't give you enough to tell "critical"
from "regular" confidently, answer "uncertain" instead of guessing -- getting
this wrong has a real cost in either direction (a mundane reminder nagging
repeatedly, or something like a flight only firing once).

Answer with exactly one word: critical, morning, regular, or uncertain. Nothing else."""

_CATEGORY_RE = re.compile(r"critical|morning|regular|uncertain")


def classify_reminder(title: str, notes: str = "") -> str:
    """Best-effort category for a new or reclassified reminder -- returns
    "uncertain" (never raises) if the API call itself fails, so a transient
    outage degrades to the disambiguation flow rather than blocking reminder
    creation or the reclassification poll."""
    try:
        client = _get_client()
        user_text = f"Title: {title}" + (f"\nNotes: {notes}" if notes else "")
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=10,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_text}],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip().lower()
        match = _CATEGORY_RE.search(text)
        return match.group(0) if match else UNCERTAIN
    except Exception:
        return UNCERTAIN


def uncertain_question_text(title: str) -> str:
    return (
        f'מנדי לא בטוח איך לסווג את התזכורת "{title}": קריטי / בוקר / רגיל? '
        f'(Mendy isn\'t sure how to categorize "{title}": critical / morning / regular?)'
    )


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state))


def queue_uncertain(group_id: str, title: str) -> None:
    """Registers a pending disambiguation question for `group_id` (a
    reminder_group id, or a raw event id for something with no group -- see
    brain/gcal.py's event_group) -- called right after creating or
    reclassifying an event the Haiku call couldn't confidently place.
    Idempotent: re-queuing the same group_id only refreshes its title, not
    its asked_at, so a later reclassification pass can't reset the
    escalation clock on a question that's already been waiting.
    """
    state = _load_state()
    existing = state.get(group_id, {})
    state[group_id] = {
        "title": title,
        "asked_at": existing.get("asked_at") or datetime.now().isoformat(),
    }
    _save_state(state)


def pending_uncertain_items() -> list[dict]:
    """[{"group_id", "title", "asked_at"}] for every reminder still awaiting
    a critical/morning/regular answer."""
    state = _load_state()
    return [{"group_id": gid, **info} for gid, info in state.items()]


def due_for_voice_escalation() -> list[dict]:
    """Subset of pending_uncertain_items() that's been waiting longer than
    UNCERTAIN_ESCALATE_AFTER_HOURS with no Telegram reply, oldest first --
    brain/llm.py surfaces these as a spoken question during the next live
    conversation."""
    cutoff = datetime.now() - timedelta(hours=UNCERTAIN_ESCALATE_AFTER_HOURS)
    due = []
    for item in pending_uncertain_items():
        try:
            asked_at = datetime.fromisoformat(item["asked_at"])
        except ValueError:
            continue
        if asked_at <= cutoff:
            due.append(item)
    due.sort(key=lambda item: item["asked_at"])
    return due


def resolve(group_id: str) -> bool:
    """Called once a household member answers (via Telegram or voice) --
    removes the pending question. Returns False if there was nothing pending
    for that group_id."""
    state = _load_state()
    if group_id not in state:
        return False
    del state[group_id]
    _save_state(state)
    return True


def find_pending_by_query(query: str) -> dict | None:
    """Matches a pending item by title substring (case-insensitive) -- used
    when a household member answers by referring to the item by name (e.g.
    Claude passing along a spoken answer) rather than its internal group id,
    which they never see."""
    query = query.strip().lower()
    if not query:
        return None
    for item in pending_uncertain_items():
        if query in item["title"].lower():
            return item
    return None
