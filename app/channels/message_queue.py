import asyncio
from typing import Callable, Awaitable
from .channel import Channel
from .message import IncomingMessage, OutgoingMessage

from ..infra.app_logging import log

# callback type: receives outbound message and delivers it
DeliveryFn = Callable[[OutgoingMessage], Awaitable[None]]


class MessageQueue:
    def __init__(self):
        self.incoming:  asyncio.Queue[IncomingMessage]  = asyncio.Queue()
        self.outgoing:  asyncio.Queue[OutgoingMessage]  = asyncio.Queue()
        self._delivery : dict[Channel, DeliveryFn] = {}

    def register(self, channel: Channel, fn: DeliveryFn):
        self._delivery[channel] = fn

    async def incoming_msg(self, message: IncomingMessage):
        log.info(f"Received incoming message for channel {message.channel}: {message.content}")
        await self.incoming.put(message)

    async def outgoing_msg(self, message: OutgoingMessage):
        await self.outgoing.put(message)

    async def process_outgoing(self):
        while True:
            message = await self.outgoing.get()
            log.info(f"Processing outgoing message for channel {message.channel}: {message.content}")
            fn = self._delivery.get(message.channel)
            if not fn:
                log.error(f"No delivery function registered for channel {message.channel}, dropping message")
                continue

            try:
                await fn(message)
            except Exception as e:
                log.error(f"Failed to deliver message to channel {message.channel}: {e}")
            