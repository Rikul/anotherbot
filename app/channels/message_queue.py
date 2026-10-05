"""Per-channel incoming/outgoing queues between a channel and its agent."""

import asyncio
from typing import Callable, Awaitable
from .channel import Channel
from .message import IncomingMessage, OutgoingMessage

from ..infra.app_logging import log

# callback type: receives outbound message and delivers it
DeliveryFn = Callable[[OutgoingMessage], Awaitable[None]]


class MessageQueue:
    """Two asyncio queues linking one channel to one ``BackgroundAgent``.

    ``incoming`` holds user messages for the agent; ``outgoing`` holds replies,
    which ``process_outgoing`` hands to the channel's registered delivery
    function. Each channel must have its own queue.
    """

    def __init__(self):
        self.incoming: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self.outgoing: asyncio.Queue[OutgoingMessage] = asyncio.Queue()
        self._delivery: dict[Channel, DeliveryFn] = {}

    def register(self, channel: Channel, fn: DeliveryFn):
        """Register ``fn`` as the delivery function for messages addressed to ``channel``."""
        self._delivery[channel] = fn

    async def incoming_msg(self, message: IncomingMessage):
        """Log and enqueue a user message for the agent."""
        log.info(
            "Received incoming message for channel %s: %s",
            message.channel,
            message.content,
        )
        await self.incoming.put(message)

    async def outgoing_msg(self, message: OutgoingMessage):
        """Enqueue a reply for delivery."""
        await self.outgoing.put(message)

    async def process_outgoing(self):
        """Deliver outgoing messages forever; delivery errors are logged and skipped."""
        while True:
            message = await self.outgoing.get()
            log.info(
                "Processing outgoing message for channel %s: %s",
                message.channel,
                message.content,
            )
            fn = self._delivery.get(message.channel)
            if not fn:
                log.error(
                    "No delivery function registered for channel %s, dropping message",
                    message.channel,
                )
                continue

            try:
                await fn(message)
            except Exception as e:  # pylint: disable=broad-exception-caught  # keep the delivery loop alive
                log.error(
                    "Failed to deliver message to channel %s: %s", message.channel, e
                )
