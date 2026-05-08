#!/bin/bash
# Restart the background agent with the latest code.
# Safe to call from within the running bot — detaches itself before killing the old process.

# Self-detach: re-exec in a new session so the kill below won't take us down
if [ -z "$_RESTART_DETACHED" ]; then
    _RESTART_DETACHED=1 nohup bash "$0" "$@" >> /tmp/restart.log 2>&1 &
    disown $!
    echo "[restart] Queued (PID $!). Logs: /tmp/restart.log"
    exit 0
fi

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_DATA_DIR="${ANOTHERBOT_HOME:-$HOME/.crafterscode}"
PID_FILE="$_DATA_DIR/background.pid"
LOG_FILE="$_DATA_DIR/background.log"
mkdir -p "$_DATA_DIR"

cd "$SCRIPT_DIR"

# Stop the existing agent
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[restart] Stopping process $OLD_PID..."
        kill "$OLD_PID"
        sleep 2
    fi
    rm -f "$PID_FILE"
else
    # Fallback: find by command line (no PID file on first run)
    pkill -f "app\.main.*background" 2>/dev/null && sleep 2 || true
fi

# Start new agent, record PID
echo "[restart] Starting background agent..."
nohup "$SCRIPT_DIR/run.sh" background >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"
echo "[restart] Started (PID $NEW_PID). Logs: $LOG_FILE"
