#!/bin/bash
# Restart the background agent with the latest code.
#
# Sends SIGHUP to the running bot, which finishes its current turns and then
# re-executes itself in place (same PID), reloading the code from disk. This
# also works from inside the bot (its bash tool) and in Docker, where killing
# the main process would stop the container. If no bot is running, or an old
# build exits on SIGHUP instead of restarting, a new one is started.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_DATA_DIR="${ANOTHERBOT_HOME:-$HOME/.anotherbot}"
PID_FILE="$_DATA_DIR/background.pid"
LOG_FILE="$_DATA_DIR/background.log"
mkdir -p "$_DATA_DIR"

cmdline() {
    if [ -r "/proc/$1/cmdline" ]; then
        tr '\0' ' ' < "/proc/$1/cmdline"
    else
        ps -o args= -p "$1" 2>/dev/null  # no /proc (macOS)
    fi
}

# The Python server itself, not the `uv run` wrapper that launched it
is_bot() {
    kill -0 "$1" 2>/dev/null && cmdline "$1" | grep -Eq "python[^ ]* (.* )?-m app\.main .*background"
}

PID=""
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    is_bot "$PID" || PID=""
fi
if [ -z "$PID" ]; then
    # No (valid) PID file: search running processes
    if [ -d /proc ]; then
        CANDIDATES=$(ls /proc | grep -E '^[0-9]+$')
    else
        CANDIDATES=$(ps -Ao pid= 2>/dev/null)
    fi
    for p in $CANDIDATES; do
        if [ "$p" != "$$" ] && is_bot "$p"; then PID=$p; break; fi
    done
fi

if [ -n "$PID" ]; then
    kill -HUP "$PID"
    echo "[restart] Restart requested (PID $PID); it restarts once running turns finish."
    exit 0
fi

echo "[restart] No running agent found; starting one..."
cd "$SCRIPT_DIR"
nohup "$SCRIPT_DIR/run.sh" background >> "$LOG_FILE" 2>&1 &
echo "[restart] Started. Logs: $LOG_FILE"
