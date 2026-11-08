#!/usr/bin/env bash
# Start Xvnc on :1 (port 5900, no password), then a window manager and a
# terminal on it. The container lives as long as Xvnc does. Arguments are
# passed on to Xvnc (e.g. -localhost when running it outside a container).
set -euo pipefail

GEOMETRY="${GEOMETRY:-1280x800}"
RFB_PORT="${RFB_PORT:-5900}"
export DISPLAY=:1

# Inside the container Xvnc listens on all interfaces (Docker's port mapping
# can't reach the container's loopback); the host side is bound to 127.0.0.1.
Xvnc :1 -geometry "$GEOMETRY" -depth 24 -SecurityTypes None -rfbport "$RFB_PORT" \
    -AlwaysShared -desktop anotherbot "$@" &
xvnc_pid=$!

for _ in $(seq 50); do
    [ -S /tmp/.X11-unix/X1 ] && break
    sleep 0.1
done

fluxbox >/dev/null 2>&1 &
xterm -geometry 160x50+10+10 &

wait "$xvnc_pid"
