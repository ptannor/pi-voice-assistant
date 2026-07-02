#!/usr/bin/env bash
# Pulls the latest pi-voice-assistant code onto the Pi over SSH and
# reinstalls dependencies. Run from a machine with SSH access to the Pi.
set -euo pipefail

CONFIG_FILE="$(dirname "$0")/.pi-config"
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

if [ -z "${PI_USER:-}" ]; then
  if [ -t 0 ]; then
    read -rp "Pi SSH username: " PI_USER
    read -rp "Save as default for next time (stored locally, not in git)? [Y/n] " save_choice
    if [[ "${save_choice:-y}" =~ ^[Yy]?$ ]]; then
      echo "PI_USER=$PI_USER" >> "$CONFIG_FILE"
      echo "Saved to $CONFIG_FILE"
    fi
  else
    echo "PI_USER not set and no terminal to prompt. Set PI_USER or create $CONFIG_FILE (see README)." >&2
    exit 1
  fi
fi

PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_DIR="${PI_DIR:-pi-voice-assistant}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/pi_voice_assistant}"

ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" bash -s -- "$PI_DIR" <<'EOF'
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$1"

if [ ! -d .venv ]; then
  echo "No .venv found in $1 — run the initial setup from the README first." >&2
  exit 1
fi

echo "Before: $(git rev-parse --short HEAD)"
git fetch origin
git pull --ff-only
uv pip install --python .venv/bin/python -r requirements.txt
echo "After:  $(git rev-parse --short HEAD)"
EOF
