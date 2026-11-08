#!/usr/bin/env bash
# Manage the throwaway desktop container for the computer-use skill.
#
#   desktop.sh start     build the image if needed and start the container
#   desktop.sh stop      stop and remove the container (all its state is lost)
#   desktop.sh status    is the container running and is VNC answering?
#   desktop.sh logs      container logs
#
# Environment: DESKTOP_NAME (default anotherbot-desktop), DESKTOP_IMAGE
# (default anotherbot-desktop), VNC_PORT (host port, default 5900),
# GEOMETRY (default 1280x800).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="${DESKTOP_NAME:-anotherbot-desktop}"
IMAGE="${DESKTOP_IMAGE:-anotherbot-desktop}"
VNC_HOST="${VNC_HOST:-127.0.0.1}"
VNC_PORT="${VNC_PORT:-5900}"
GEOMETRY="${GEOMETRY:-1280x800}"

need_docker() {
    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
        echo "error: Docker is not available here." >&2
        echo "If anotherbot runs in Docker Compose, start the desktop sidecar on the host instead:" >&2
        echo "  docker compose --profile desktop up -d desktop   (then use VNC_HOST=desktop)" >&2
        exit 1
    fi
}

vnc_up() {
    # An RFB server greets with "RFB 003.00x\n" as soon as you connect.
    local greeting
    greeting="$(timeout 3 bash -c "exec 3<>/dev/tcp/$VNC_HOST/$VNC_PORT && head -c 12 <&3" 2>/dev/null || true)"
    [[ "$greeting" == RFB* ]]
}

case "${1:-}" in
start)
    need_docker
    if [ -n "$(docker ps -q --filter "name=^${NAME}$")" ]; then
        echo "already running: $NAME"
    else
        docker rm -f "$NAME" >/dev/null 2>&1 || true
        if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
            docker build -q -t "$IMAGE" "$HERE/desktop" >/dev/null
        fi
        # Loopback only: the VNC server has no password.
        docker run -d --rm --name "$NAME" --shm-size 512m \
            -e GEOMETRY="$GEOMETRY" \
            -p "127.0.0.1:${VNC_PORT}:5900" "$IMAGE" >/dev/null
        echo "started: $NAME"
    fi
    for _ in $(seq 30); do
        if vnc_up; then
            echo "VNC ready at $VNC_HOST:$VNC_PORT"
            exit 0
        fi
        sleep 1
    done
    echo "error: VNC did not answer at $VNC_HOST:$VNC_PORT; see: $0 logs" >&2
    exit 1
    ;;
stop)
    need_docker
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "stopped: $NAME" || echo "not running: $NAME"
    ;;
status)
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        docker ps --filter "name=^${NAME}$" --format '{{.Names}} {{.Status}}'
    fi
    if vnc_up; then echo "VNC answering at $VNC_HOST:$VNC_PORT"; else echo "VNC not answering at $VNC_HOST:$VNC_PORT"; exit 1; fi
    ;;
logs)
    need_docker
    docker logs "$NAME"
    ;;
*)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
