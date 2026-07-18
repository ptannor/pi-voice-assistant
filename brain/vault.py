"""Encrypted key-value store for genuinely sensitive household facts (bank
account numbers, safe codes, etc.) -- a separate tier from brain/memory.py's
plaintext core.txt/reference/, for anything that shouldn't sit in the clear
even inside the already-gitignored household_memory/ directory.

AES-256-GCM (cryptography's AESGCM -- not Fernet, which defaults to AES-128-
CBC) with a single master key loaded from MEMORY_VAULT_KEY (see
brain/config.py's comment for the threat model this accepts: protects against
the Pi/SD-card being lost, stolen, or a backup leaking, NOT a live-
compromised running Pi, since the daemon must decrypt unattended at boot with
no passphrase entry).

Whole-file re-encrypt on every write -- fine at household scale (a handful of
secrets, not a database's worth), and much simpler than a partial-update
scheme. brain/memory.py's remember() calls looks_sensitive() to decide
whether a fact should be routed here instead of plain memory, prompting Claude
to ask before storing rather than silently saving something sensitive in the
clear -- see that function's docstring.

On top of the encryption, store/retrieve/forget also require a spoken
household password (VAULT_ACCESS_PASSWORD, see unlock() below) once per
conversation -- a lightweight access gate, not a cryptographic control (it
doesn't affect what's on disk), so someone glancing at the running device
mid-conversation can't fish for a stored secret just by asking.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import backup_trigger
from .config import MEMORY_VAULT_KEY, VAULT_ACCESS_PASSWORD

VAULT_PATH = Path(__file__).parent.parent / "household_memory" / "vault.enc"
_AAD = b"mendy-vault-v1"
_NONCE_SIZE = 12

_SENSITIVE_KEYWORDS = (
    "password", "passcode", "pin", "cvv", "iban", "account number",
    "routing number", "social security", "ssn", "credit card", "debit card",
    "bank account", "safe code",
)
# A run of 8+ consecutive digits -- card/account/IBAN-shaped numbers.
# Deliberately a broad safety-net trigger for the ask-before-storing flow (see
# memory.remember), not a precise PII classifier: a false positive just costs
# one extra confirmation question, not a wrongly-stored secret.
_DIGIT_RUN = re.compile(r"\d{8,}")


def looks_sensitive(text: str) -> bool:
    if _DIGIT_RUN.search(text):
        return True
    lowered = text.lower()
    return any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS)


def _load_key() -> bytes | None:
    if not MEMORY_VAULT_KEY:
        return None
    try:
        key = base64.b64decode(MEMORY_VAULT_KEY)
    except (ValueError, TypeError):
        return None
    return key if len(key) == 32 else None


def _read_vault() -> dict[str, str]:
    key = _load_key()
    if key is None or not VAULT_PATH.exists():
        return {}
    raw = VAULT_PATH.read_bytes()
    nonce, ciphertext = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _AAD)
    except InvalidTag:
        return {}
    return json.loads(plaintext)


def _write_vault(data: dict[str, str]) -> None:
    key = _load_key()
    if key is None:
        return
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, json.dumps(data).encode(), _AAD)
    VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write-to-temp-then-rename so a crash mid-write can never leave vault.enc
    # half-written/corrupted -- os.replace is atomic on both POSIX and the Pi.
    tmp_path = VAULT_PATH.with_suffix(".tmp")
    tmp_path.write_bytes(nonce + ciphertext)
    os.replace(tmp_path, VAULT_PATH)
    backup_trigger.trigger()


_NOT_CONFIGURED = "Vault not configured -- MEMORY_VAULT_KEY is missing or invalid. Tell the user this needs setup before secrets can be stored securely."
_LOCKED = "The vault is locked for this conversation. Ask the user for the vault password, then call unlock_vault with what they say."

# Whether the vault has been unlocked this conversation -- module-level and
# single-process, matching this codebase's existing style for daemon-wide
# state (e.g. brain/reminders.py's _critical_pending). Reset by lock() at the
# start of every new conversation (see brain/llm.py's ask()), so the
# password is required again each session rather than just once ever.
_unlocked = False


def lock() -> None:
    global _unlocked
    _unlocked = False


def unlock(password: str) -> str:
    global _unlocked
    if not VAULT_ACCESS_PASSWORD:
        return "No vault password is configured -- tell the user this needs setup first."
    if password.strip().lower() == VAULT_ACCESS_PASSWORD.strip().lower():
        _unlocked = True
        return "Unlocked."
    return "Incorrect password."


def store_secret(label: str, value: str) -> str:
    if not _unlocked:
        return _LOCKED
    if _load_key() is None:
        return _NOT_CONFIGURED
    label, value = label.strip(), value.strip()
    if not label or not value:
        return "Nothing to store -- both a label and a value are needed."
    data = _read_vault()
    data[label] = value
    _write_vault(data)
    return f"Stored '{label}' securely in the vault."


def retrieve_secret(query: str) -> str:
    if not _unlocked:
        return _LOCKED
    if _load_key() is None:
        return _NOT_CONFIGURED
    query = query.strip().lower()
    if not query:
        return "Nothing specified to look up."
    matches = {label: value for label, value in _read_vault().items() if query in label.lower()}
    if not matches:
        return "No matching vault entry found."
    return "; ".join(f"{label}: {value}" for label, value in matches.items())


def forget_secret(query: str) -> str:
    if not _unlocked:
        return _LOCKED
    if _load_key() is None:
        return _NOT_CONFIGURED
    query = query.strip().lower()
    if not query:
        return "Nothing specified to forget."
    data = _read_vault()
    matching_labels = [label for label in data if query in label.lower()]
    if not matching_labels:
        return "No matching vault entry found."
    for label in matching_labels:
        del data[label]
    _write_vault(data)
    return f"Forgot: {', '.join(matching_labels)}"


def list_labels() -> list[str]:
    """Labels only, never values -- for the (optional) periodic privacy audit,
    which must be able to summarize what's stored without exposing secrets.
    """
    return sorted(_read_vault().keys())


def _init_key() -> None:
    key = base64.b64encode(os.urandom(32)).decode()
    print("Add this line to your .env (never commit it), then clear your terminal")
    print("scrollback/history afterward -- it's shown here in plain text once:\n")
    print(f"MEMORY_VAULT_KEY={key}\n")
    print(
        "Also store a copy of this key somewhere off the Pi (password manager, etc.) -- "
        "it is NOT included in the encrypted household_memory/ backup (see "
        "scripts/backup_memory.sh), so losing it makes vault.enc unrecoverable."
    )


if __name__ == "__main__":
    if "--init" in sys.argv:
        _init_key()
    else:
        print("Usage: python -m brain.vault --init")
