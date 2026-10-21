"""In-place restart of the background server.

A restart is requested by the ``/restart`` command or by ``SIGHUP`` (which
``restart.sh`` sends). ``start_server()`` then waits for running agent turns
to finish, lets queued replies go out, and returns; ``app.main`` re-executes
the same command line in the same process (``os.execv``). Keeping the PID
matters: killing the main process and starting a new one fails in Docker
(PID 1 exiting stops the container, killing the restart script with it) and
under process supervisors that kill the whole process group.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .app_logging import log

# Repo root (holds pyproject.toml); `uv sync` runs against it before re-exec.
PROJECT_DIR = Path(__file__).resolve().parents[2]
# start_server() chdirs into the workspace; run.sh may have set a relative
# PYTHONPATH, so the new process must start from the original directory.
START_CWD = os.getcwd()


class _State:  # pylint: disable=too-few-public-methods  # plain process-wide state
    requested = False
    event: asyncio.Event | None = None
    active_turns = 0


_state = _State()


def request_restart() -> None:
    """Ask the running server to restart once current turns finish."""
    log.info("Restart requested")
    _state.requested = True
    if _state.event is not None:
        _state.event.set()


def requested() -> bool:
    """Return whether a restart has been requested."""
    return _state.requested


def reset() -> None:
    """Clear the restart state (used by tests)."""
    _state.requested = False
    _state.event = None
    _state.active_turns = 0


async def wait_requested() -> None:
    """Block until a restart is requested."""
    _state.event = asyncio.Event()  # bound to the running loop
    if _state.requested:
        _state.event.set()
    await _state.event.wait()


@contextlib.contextmanager
def turn():
    """Mark an agent turn as running, so a restart waits for it to finish."""
    _state.active_turns += 1
    try:
        yield
    finally:
        _state.active_turns -= 1


async def wait_until_idle(outgoing_queues=(), timeout: float = 300.0) -> None:
    """Wait (up to ``timeout`` seconds) for running turns to end and replies to be sent."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if _state.active_turns == 0 and all(q.empty() for q in outgoing_queues):
            break
        await asyncio.sleep(0.5)
    else:
        log.warning("Restarting with %s turn(s) still running", _state.active_turns)
    # The last dequeued reply may still be in flight.
    await asyncio.sleep(1)


def _sync_dependencies() -> None:
    """Install dependencies the new code may need, as ``uv run`` does on startup."""
    uv = shutil.which("uv")
    if not uv or not (PROJECT_DIR / "pyproject.toml").exists():
        return
    try:
        result = subprocess.run(
            [uv, "sync", "--inexact", "--project", str(PROJECT_DIR)],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught  # restart anyway
        log.warning("uv sync failed before restart: %s", e)
        return
    if result.returncode != 0:
        log.warning("uv sync failed before restart: %s", result.stderr.strip())


def exec_restart() -> None:
    """Replace the current process with a fresh run of the same command line."""
    _sync_dependencies()
    argv = list(sys.orig_argv)
    log.info("Restarting: %s", " ".join(argv))
    logging.shutdown()
    os.chdir(START_CWD)
    os.execv(sys.executable, argv)
