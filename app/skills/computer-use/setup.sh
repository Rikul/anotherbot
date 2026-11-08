#!/usr/bin/env bash
# One-time setup for the computer-use skill: a venv with the Anthropic SDK and
# the example VNC driver from anthropics/claude-quickstarts (computer-toolset).
# Idempotent; prints the venv's python path on success.
#
# Environment: COMPUTER_USE_HOME (default $ANOTHERBOT_HOME/computer-use, or
# ~/.anotherbot/computer-use), QUICKSTARTS_REF (git commit to fetch).
set -euo pipefail

HOME_DIR="${COMPUTER_USE_HOME:-${ANOTHERBOT_HOME:-$HOME/.anotherbot}/computer-use}"
# Pinned so the driver doesn't change under us; bump deliberately.
REF="${QUICKSTARTS_REF:-4a4224bdf0e9cfb3332167b69c90457403813625}"
BASE="https://raw.githubusercontent.com/anthropics/claude-quickstarts/$REF/computer-toolset/python"

mkdir -p "$HOME_DIR/driver"
cd "$HOME_DIR"

if [ ! -f "driver/.ref" ] || [ "$(cat driver/.ref)" != "$REF" ]; then
    for f in vnc_computer.py exercise.py run.py requirements.txt; do
        curl -fsSL "$BASE/$f" -o "driver/$f"
    done
    echo "$REF" > driver/.ref
    rm -f .venv/.installed
fi

if [ ! -f .venv/.installed ]; then
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r driver/requirements.txt
    touch .venv/.installed
fi

echo "$HOME_DIR/.venv/bin/python"
