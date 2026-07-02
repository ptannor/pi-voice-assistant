#!/usr/bin/env bash
# Pulls the latest pi-voice-assistant code onto the Pi over SSH and
# reinstalls dependencies. Run from a machine with SSH access to the Pi.
set -euo pipefail

PI_USER="${PI_USER:-philip}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_DIR="${PI_DIR:-pi-voice-assistant}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/pi_voice_assistant}"

ssh -i "$SSH_KEY" "$PI_USER@$PI_HOST" bash -s -- "$PI_DIR" <<'EOF'
set -euo pipefail
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
