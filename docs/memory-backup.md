# Runbook: memory vault + off-Pi backup setup and restore

Covers one-time setup and disaster recovery for `household_memory/` (plaintext
core.txt/reference/, plus the encrypted `vault.enc` -- see `brain/vault.py`).
Not needed to just use memory/vault day-to-day; only for initial setup and if
the Pi itself ever needs replacing.

## One-time setup

1. **Generate the vault key:**
   ```
   uv run python -m brain.vault --init
   ```
   Paste the printed `MEMORY_VAULT_KEY=...` line into `.env`, then clear your
   terminal scrollback -- it was just shown in plain text.

2. **Store the vault key off the Pi.** Put a copy in a password manager (or
   written down somewhere safe). This is required, not optional: the key
   lives in `.env`, which is outside `household_memory/`, so it is **not**
   included in the backup below. Lose it and `vault.enc` is unrecoverable,
   even if the backup itself is intact.

3. **Set up an rclone crypt remote** for the backup destination (any object
   storage you control -- Backblaze B2's free tier or a Google Drive folder
   both work fine at this data size):
   ```
   rclone config
   ```
   Create a remote for your actual storage (e.g. `mendy-b2`), then wrap it in
   a second, `crypt`-type remote named `mendy-backup` that encrypts
   client-side before upload -- point it at the first remote as its backend.
   Store the crypt remote's password off the Pi too (same reasoning as the
   vault key -- needed to decrypt a restored backup on a new Pi).

4. **Enable the backup timer:**
   ```
   systemctl --user enable --now pi-memory-backup.timer
   ```
   From then on, backups run daily and immediately after any memory/vault
   write (see `brain/backup_trigger.py`).

## Restoring onto a new/replacement Pi

1. Set up the new Pi per the main README, but stop before starting the voice
   assistant service.
2. Put the vault key back in `.env` (`MEMORY_VAULT_KEY=...`, from wherever you
   stored it in step 2 above).
3. Configure the same rclone crypt remote (`rclone config`, using the stored
   crypt password).
4. Restore into a **fresh** `household_memory/` directory -- don't sync over
   a half-populated one:
   ```
   rclone sync mendy-backup:mendy-memory household_memory/
   ```
5. Start the voice assistant normally. Confirm a known remembered fact and a
   known vault secret are both retrievable before considering the restore
   complete.

## What this protects against, and what it doesn't

Protects against: the Pi/SD-card being damaged, lost, or stolen, and against
a leaked/exposed backup copy (client-side encrypted before it leaves the
Pi). Does **not** protect against a live-compromised running Pi -- the vault
key must be readable by the running daemon with no passphrase prompt at
boot, so anyone with code execution on a running Pi can read it. This is an
accepted tradeoff for an unattended household device, not an oversight.
