"""Tests for the bash tool (real, short shell commands)."""

import asyncio
import os
import time

import pytest

from app.tools.bash import BashTool

bash = BashTool.call


@pytest.mark.asyncio
async def test_bash_runs_simple_command():
    assert "hello" in await bash("echo hello")


@pytest.mark.asyncio
async def test_bash_returns_stderr_on_failure():
    result = await bash("ls /nonexistent_path_xyz")
    assert "[stderr]" in result
    assert "No such file" in result or "cannot access" in result


@pytest.mark.asyncio
async def test_bash_captures_stdout():
    assert await bash("printf 'abc'") == "abc"


@pytest.mark.asyncio
async def test_bash_timeout_error():
    start = time.monotonic()
    result = await bash("sleep 100", timeout=1)
    assert "timed out" in result and "Error" in result
    assert time.monotonic() - start < 5


@pytest.mark.asyncio
async def test_bash_multiline_output():
    assert await bash("printf 'a\\nb\\nc'") == "a\nb\nc"


@pytest.mark.asyncio
async def test_bash_returns_combined_stderr():
    result = await bash("echo out && echo err >&2")
    assert "out" in result
    assert "[stderr]\nerr" in result


@pytest.mark.asyncio
async def test_bash_cancel_kills_the_command(tmp_path):
    """Cancelling the call (Ctrl+C in the TUI) kills the shell and its children."""
    marker = tmp_path / "pid"
    task = asyncio.ensure_future(bash(f"sleep 30 & echo $! > {marker}; wait"))
    for _ in range(100):
        if marker.exists() and marker.read_text().strip():
            break
        await asyncio.sleep(0.02)
    child = int(marker.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("the backgrounded sleep survived the cancel")


@pytest.mark.asyncio
async def test_kill_uses_taskkill_on_windows():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.tools import bash as bash_mod

    proc = MagicMock(pid=1234, returncode=None)
    proc.wait = AsyncMock(return_value=0)
    killer = MagicMock()
    killer.wait = AsyncMock(return_value=0)
    with (
        patch.object(bash_mod.os, "name", "nt"),
        patch.object(
            bash_mod.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=killer),
        ) as exec_,
    ):
        await bash_mod._kill(proc)
    assert exec_.await_args.args == ("taskkill", "/F", "/T", "/PID", "1234")
    proc.kill.assert_called_once()  # fallback for the shell itself


@pytest.mark.asyncio
async def test_kill_lets_cancellation_through():
    """A Ctrl+C during the post-kill wait must not be swallowed."""
    from unittest.mock import MagicMock, patch

    from app.tools import bash as bash_mod

    async def never():
        await asyncio.sleep(10)

    proc = MagicMock(pid=999999, returncode=0)
    proc.wait = never
    with patch.object(bash_mod.os, "killpg"):
        task = asyncio.ensure_future(bash_mod._kill(proc))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
