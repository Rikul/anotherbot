import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, patch

import pytest

from app.channels.channel import Channel, ChannelType
from app.channels.message import OutgoingMessage
from app.channels.message_queue import MessageQueue


class DummyChannel(Channel):
    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.CLI

    @property
    def has_stopped(self) -> bool:
        return False

    async def send_message(self, message) -> None:
        return None

    async def process_message(self, message) -> None:
        return None

    def error_handler(self, update, context) -> None:
        return None

    def clear_stopped(self) -> None:
        return None


async def _run_until_queue_drained(queue: MessageQueue):
    task = asyncio.create_task(queue.process_outgoing())
    try:
        await asyncio.wait_for(queue.outgoing.join(), timeout=1)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_process_outgoing_marks_queue_done_after_success():
    queue = MessageQueue()
    channel = DummyChannel()
    deliver = AsyncMock()
    queue.register(channel, deliver)
    await queue.outgoing_msg(OutgoingMessage(content="hi", channel=channel))

    await _run_until_queue_drained(queue)

    deliver.assert_awaited_once()
    assert queue.outgoing.empty()


@pytest.mark.asyncio
async def test_process_outgoing_marks_queue_done_when_handler_missing():
    queue = MessageQueue()
    channel = DummyChannel()
    await queue.outgoing_msg(OutgoingMessage(content="hi", channel=channel))

    await _run_until_queue_drained(queue)

    assert queue.outgoing.empty()


@pytest.mark.asyncio
async def test_process_outgoing_marks_queue_done_when_delivery_fails():
    queue = MessageQueue()
    channel = DummyChannel()
    deliver = AsyncMock(side_effect=RuntimeError("boom"))
    queue.register(channel, deliver)
    await queue.outgoing_msg(OutgoingMessage(content="hi", channel=channel))

    with patch("app.channels.message_queue.log.error") as error_log:
        await _run_until_queue_drained(queue)

    deliver.assert_awaited_once()
    assert "Failed to deliver message" in error_log.call_args.args[0]
    assert queue.outgoing.empty()
