#!/bin/bash
# Restart the background agent with the latest code.
#
# Sends SIGHUP to the running bot, which finishes its current turns and then
# re-executes itself in place (same PID), reloading the code from disk. This
# works from inside the bot (its bash tool) and in Docker, where killing the
# main process would stop the container. The script then checks that the bot
# came back (it rewrites the PID file on startup) and starts a new one if it
# exited instead. A bot that predates in-place restarts (it never wrote the
# PID file itself) is stopped and started again, the old way.

# Detach so the checks below survive the bot (and its bash tool's timeout)
if [ -z "$_RESTART_DETACHED" ]; then
    LOG="${ANOTHERBOT_HOME:-$HOME/.anotherbot}/restart.log"
    mkdir -p "$(dirname "$LOG")"
    if command -v setsid >/dev/null 2>&1; then
        _RESTART_DETACHED=1 setsid nohup bash "$0" "$@" >> "$LOG" 2>&1 < /dev/null &
    else
        _RESTART_DETACHED=1 nohup bash "$0" "$@" >> "$LOG" 2>&1 < /dev/null &
    fi
    echo "[restart] Restart queued; the bot restarts once running turns finish. Log: $LOG"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_DATA_DIR="${ANOTHERBOT_HOME:-$HOME/.anotherbot}"
mkdir -p "$_DATA_DIR"
_DATA_DIR="$(cd "$_DATA_DIR" && pwd -P)"
PID_FILE="$_DATA_DIR/background.pid"
LOG_FILE="$_DATA_DIR/background.log"
WAIT_SECS="${RESTART_WAIT_SECS:-600}"  # running turns may take a while

say() { echo "[restart $(date '+%F %T')] $*"; }

cmdline() {
    if [ -r "/proc/$1/cmdline" ]; then
        tr '\0' ' ' < "/proc/$1/cmdline"
    else
        ps -o args= -p "$1" 2>/dev/null  # no /proc (macOS)
    fi
}

# The Python server for THIS data dir (it chdirs into $_DATA_DIR/workspace),
# not the `uv run` wrapper and not another checkout's bot.
is_bot() {
    kill -0 "$1" 2>/dev/null || return 1
    cmdline "$1" | grep -Eq "python[^ ]* .*app(\.|/)main(\.py)? .*background" || return 1
    if [ -d "/proc/$1" ]; then
        [ "$(readlink "/proc/$1/cwd" 2>/dev/null)" = "$_DATA_DIR/workspace" ] || return 1
    fi
}

find_bot() {
    if [ -d /proc ]; then
        CANDIDATES=$(ls /proc | grep -E '^[0-9]+$')
    else
        CANDIDATES=$(ps -Ao pid= 2>/dev/null)
    fi
    for p in $CANDIDATES; do
        if [ "$p" != "$$" ] && is_bot "$p"; then echo "$p"; return; fi
    done
}

file_mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0; }

start_bot() {
    say "Starting background agent..."
    cd "$SCRIPT_DIR" || exit 1
    nohup "$SCRIPT_DIR/run.sh" background >> "$LOG_FILE" 2>&1 < /dev/null &
    say "Started. Logs: $LOG_FILE"
}

stop_pid() {
    kill "$1" 2>/dev/null
    for _ in $(seq 20); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.5; done
    say "PID $1 ignored SIGTERM; killing it"
    kill -9 "$1" 2>/dev/null
    sleep 1
}

PID=""
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    is_bot "$PID" || PID=""
fi

if [ -n "$PID" ]; then
    # Current build: restart in place, then confirm it came back
    BEFORE=$(file_mtime "$PID_FILE")
    sleep 1  # so a rewrite of the PID file gets a newer mtime
    kill -HUP "$PID"
    say "Restart requested (PID $PID); waiting for it to come back..."
    for _ in $(seq "$WAIT_SECS"); do
        sleep 1
        if ! kill -0 "$PID" 2>/dev/null; then
            say "PID $PID exited instead of restarting"
            start_bot
            exit 0
        fi
        if [ "$(file_mtime "$PID_FILE")" != "$BEFORE" ]; then
            say "Restarted (PID $PID)."
            exit 0
        fi
    done
    say "PID $PID has not restarted after ${WAIT_SECS}s (still finishing a turn?); leaving it running."
    exit 1
fi

OLD=$(find_bot)
if [ -n "$OLD" ]; then
    # No PID file written by the bot itself: a build without in-place restart
    say "Stopping PID $OLD (no in-place restart support)..."
    stop_pid "$OLD"
fi
start_bot
