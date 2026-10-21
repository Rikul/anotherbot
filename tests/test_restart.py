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
async def test_wait_until_idle_waits_for_replies_to_be_delivered():
    """A reply already taken off the queue but still being sent counts as busy."""
    from app.channels.message import OutgoingMessage
    from app.channels.message_queue import MessageQueue

    mq = MessageQueue()
    channel = object()
    delivered = []

    async def slow_delivery(msg):
        await asyncio.sleep(0.5)
        delivered.append(msg.content)

    mq.register(channel, slow_delivery)
    await mq.outgoing_msg(OutgoingMessage(content="bye", channel=channel))
    consumer = asyncio.create_task(mq.process_outgoing())
    await asyncio.sleep(0.05)
    assert mq.outgoing.empty() and not mq.outgoing_idle()  # dequeued, still sending

    await restart.wait_until_idle([mq], timeout=5)
    assert delivered == ["bye"]
    consumer.cancel()


@pytest.mark.asyncio
async def test_sighup_handler_survives_loop_close_and_requests_restart():
    import os
    import signal

    loop = asyncio.get_running_loop()
    previous = restart.install_sighup_handler(loop)
    try:
        os.kill(os.getpid(), signal.SIGHUP)
        await asyncio.sleep(0.1)
        assert restart.requested()
        # A second SIGHUP while restarting is ignored, not fatal
        os.kill(os.getpid(), signal.SIGHUP)
        await asyncio.sleep(0.1)
    finally:
        signal.signal(signal.SIGHUP, previous)


def test_exec_restart_ignores_sighup_across_exec():
    import signal

    previous = signal.getsignal(signal.SIGHUP)
    try:
        with patch("app.infra.restart._sync_dependencies"), \
             patch("app.infra.restart.os.chdir"), \
             patch("app.infra.restart.logging.shutdown"), \
             patch("app.infra.restart.os.execv"):
            restart.exec_restart()
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, previous)


def test_sync_dependencies_mirrors_the_image_build():
    with patch("app.infra.restart.shutil.which", return_value="/usr/bin/uv"), \
         patch("app.infra.restart.subprocess.run") as run:
        run.return_value.returncode = 0
        restart._sync_dependencies()
    args = run.call_args[0][0]
    assert {"--frozen", "--no-dev", "--inexact"} <= set(args)


def test_exec_restart_reexecs_same_command_from_start_dir():
    import signal
    previous = signal.getsignal(signal.SIGHUP)
    with patch("app.infra.restart._sync_dependencies") as sync, \
         patch("app.infra.restart.os.chdir") as chdir, \
         patch("app.infra.restart.logging.shutdown"), \
         patch("app.infra.restart.os.execv") as execv:
        restart.exec_restart()
    sync.assert_called_once()
    chdir.assert_called_once_with(restart.START_CWD)
    execv.assert_called_once_with(sys.executable, list(sys.orig_argv))
    signal.signal(signal.SIGHUP, previous)


def test_sync_dependencies_failure_does_not_raise():
    with patch("app.infra.restart.shutil.which", return_value="/usr/bin/uv"), \
         patch("app.infra.restart.subprocess.run", side_effect=OSError("nope")):
        restart._sync_dependencies()
