import asyncio
import sys
from unittest.mock import patch

import pytest

from app.infra import restart


@pytest.mark.asyncio
async def test_wait_requested_returns_if_already_requested():
    restart.request_restart()
    await asyncio.wait_for(restart.wait_requested(), 1)


@pytest.mark.asyncio
async def test_wait_requested_wakes_on_request():
    waiter = asyncio.create_task(restart.wait_requested())
    await asyncio.sleep(0)
    assert not waiter.done()
    restart.request_restart()
    await asyncio.wait_for(waiter, 1)


@pytest.mark.asyncio
async def test_wait_until_idle_waits_for_running_turns():
    finished = []

    async def a_turn():
        with restart.turn():
            await asyncio.sleep(0.3)
            finished.append(True)

    task = asyncio.create_task(a_turn())
    await asyncio.sleep(0)
    await restart.wait_until_idle(timeout=5)
    assert finished == [True]
    await task


@pytest.mark.asyncio
async def test_wait_until_idle_waits_for_outgoing_queue():
    q = asyncio.Queue()
    q.put_nowait("reply")
    asyncio.get_running_loop().call_later(0.2, q.get_nowait)
    await restart.wait_until_idle([q], timeout=5)
    assert q.empty()


def test_exec_restart_reexecs_same_command_from_start_dir():
    with patch("app.infra.restart._sync_dependencies") as sync, \
         patch("app.infra.restart.os.chdir") as chdir, \
         patch("app.infra.restart.logging.shutdown"), \
         patch("app.infra.restart.os.execv") as execv:
        restart.exec_restart()
    sync.assert_called_once()
    chdir.assert_called_once_with(restart.START_CWD)
    execv.assert_called_once_with(sys.executable, list(sys.orig_argv))


def test_sync_dependencies_failure_does_not_raise():
    with patch("app.infra.restart.shutil.which", return_value="/usr/bin/uv"), \
         patch("app.infra.restart.subprocess.run", side_effect=OSError("nope")):
        restart._sync_dependencies()
