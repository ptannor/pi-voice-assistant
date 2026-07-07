"""Long-term household memory -- facts Claude should carry across
conversations, not just within one session (brain/llm.py's `history` resets
every time the wake word starts a new conversation).

Two tiers, both under the gitignored `household_memory/` directory at the
repo root (real household data, not something that belongs in a public
repo):

- `core.txt`: small facts/preferences (names, allergies, house rules).
  Plain text, one per line, injected into *every* system prompt (see
  memory_prompt_block()) -- keep this tier small on purpose. Curate by
  hand (any text editor over SSH) or via voice through the `remember` /
  `forget` tools.
- `reference/`: anything bigger (recipes, birthday lists, family member
  profiles, school/activity schedules -- whatever). Not injected into every
  prompt (would bloat cost/latency for turns that have nothing to do with
  it); instead searched on demand via the `search_household_info` tool,
  which does a plain case-insensitive substring search across every file in
  this directory and returns whole matching files. Add files here directly
  (any format, any structure) -- there's deliberately no schema imposed yet.

Open questions, deliberately deferred until there's real data to design
against rather than a guessed shape: how birthdays/schedules should be
structured (plain text vs. CSV vs. native Excel -- the last needs adding
`openpyxl` as a dependency, not decided yet), and whether reference material
ever needs its own write-tool (right now it's curated by hand, matching how
the user actually plans to add it -- as files).
"""
from __future__ import annotations

import re
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent / "household_memory"
CORE_PATH = MEMORY_DIR / "core.txt"
REFERENCE_DIR = MEMORY_DIR / "reference"


def load_memories() -> list[str]:
    if not CORE_PATH.exists():
        return []
    lines = CORE_PATH.read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _save_memories(memories: list[str]) -> None:
    CORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORE_PATH.write_text("\n".join(memories) + ("\n" if memories else ""))


def remember(fact: str) -> str:
    fact = fact.strip()
    if not fact:
        return "Nothing to remember -- the fact was empty."
    memories = load_memories()
    if any(existing.lower() == fact.lower() for existing in memories):
        return "Already remembered -- no change."
    memories.append(fact)
    _save_memories(memories)
    return "Remembered."


def forget(query: str) -> str:
    """Removes any remembered fact whose text contains `query` (case-insensitive).
    Best-effort substring match -- fine at this scale (a person reviewing what
    matched, via the tool result, is the real safeguard against removing the
    wrong thing, not a fancier match algorithm).
    """
    query = query.strip().lower()
    if not query:
        return "Nothing specified to forget."
    memories = load_memories()
    matches = [m for m in memories if query in m.lower()]
    if not matches:
        return "No matching remembered fact found."
    remaining = [m for m in memories if m not in matches]
    _save_memories(remaining)
    return f"Forgot: {'; '.join(matches)}"


def memory_prompt_block() -> str:
    """A system-prompt-ready block of everything currently remembered (core
    tier only), or an empty string if there's nothing yet. Loaded fresh per
    request (see brain/llm.py) so a fact saved mid-conversation is visible
    immediately.
    """
    memories = load_memories()
    if not memories:
        return ""
    bullet_list = "\n".join(f"- {m}" for m in memories)
    return f"\nThings you've been told to remember about this household:\n{bullet_list}\n"


def search_household_info(query: str) -> str:
    """Case-insensitive substring search across every file under
    `reference/`, returning whole files that match -- no chunking or
    per-format parsing yet (see module docstring for why: no real data to
    design that against). Fine at the file sizes a household actually
    produces (a recipe, a person's profile, a schedule note).

    Matches per-word, not the whole query as one substring -- confirmed
    Claude sends natural-language queries like "lasagna baking time recipe"
    rather than a single keyword, which a whole-phrase match would almost
    always miss even when the file plainly contains "lasagna".
    """
    query = query.strip().lower()
    if not query:
        return "No search query given."
    if not REFERENCE_DIR.exists():
        return "No household reference files have been added yet."

    words = [w for w in re.findall(r"\w+", query, flags=re.UNICODE) if len(w) > 2]
    if not words:
        return "No matching household reference info found."

    matches = []
    for path in sorted(REFERENCE_DIR.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        haystack = f"{text}\n{path.stem}".lower()
        if any(word in haystack for word in words):
            matches.append(f"--- {path.relative_to(REFERENCE_DIR)} ---\n{text.strip()}")

    if not matches:
        return "No matching household reference info found."
    return "\n\n".join(matches)
