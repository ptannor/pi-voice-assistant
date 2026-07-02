---
name: ssh-pi
description: Connect to or run commands on the pi-voice-assistant Raspberry Pi over SSH. Use when asked to check something on the Pi, run a command there, debug Pi state, or help with SSH access to it.
---

# SSH into the pi-voice-assistant Raspberry Pi

Resolve connection details the same way `update-pi.sh` does, in this order:

1. `.pi-config` in the repo root (gitignored, personal — may set `PI_USER`,
   `PI_HOST`, `PI_DIR`, `SSH_KEY`)
2. Defaults: `PI_HOST=raspberrypi.local`, `SSH_KEY=$HOME/.ssh/pi_voice_assistant`
3. `PI_USER` has no safe default — if it's not in `.pi-config` and not set as
   an env var, ask the user for it rather than guessing.

Run a one-off command:

```bash
ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" "<command>"
```

Key-based auth is already set up, so commands run fine non-interactively
(including through a relay without a real TTY) — no password prompt involved.

## Read-only vs. state-changing

Diagnostic and read-only commands (checking versions, `uv run main.py
list-devices`, `git status`/`git log`, `arecord -l`/`aplay -l`, viewing
files) can run directly — just state the exact command before running it.

Anything that changes state on the Pi — installing packages, editing files,
git operations beyond read-only ones, restarting services, or running
`update-pi.sh` — **must stop and get explicit approval first**, every time,
even mid-conversation. This is a standing user preference, not a one-off: see
the "confirm before git/deploy actions" project memory.

## If the connection itself is broken

Don't start guessing — the setup and common failure modes are documented in
the pi-voice-assistant README:
- SSH key setup: `ssh-keygen`, `ssh-keyscan` (to avoid interactive host-key
  prompts), `ssh-copy-id` (must run in a real terminal with a TTY, not a
  relay without one)
- `update-pi.sh` troubleshooting section: wrong clone path (`PI_DIR`), `uv:
  command not found` over non-interactive SSH (PATH issue)
- Bluetooth/audio-specific issues: see the Bluetooth speaker setup and
  Troubleshooting sections

Read those sections first; only propose something new if the documented
fixes don't apply.
