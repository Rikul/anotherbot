from __future__ import annotations

import asyncio

from .tool_calls import all_tool_specs
from ..infra.app_logging import log
from ..channels.channel import Channel
from ..channels.message import OutgoingMessage
from ..channels.message_queue import MessageQueue
from .agent import Agent, MAX_CONTEXT_MESSAGES
from ..infra.startup import load_system_context
from ..infra.message_history import MessageHistory

_MAX_EMPTY_RETRIES = 5


class BackgroundAgent(Agent):

    def __init__(self, mq: MessageQueue = None, channel: Channel = None, max_iterations: int = 250) -> None:
        super().__init__(max_iterations)
        self.mq = mq
        self.channel = channel

        if self.channel is None:
            raise ValueError("channel must be specified for BackgroundAgent")

        self.history = MessageHistory(channel_type=channel.channel_type.value)
        self.messages.extend(self.history.get_history(limit=MAX_CONTEXT_MESSAGES))
        self._reply_metadata: dict = {}
        self._empty_retries: int = 0

    async def _on_thinking(self, content: str | None) -> None:
        if self.mq and content:
            text = content.strip().rstrip(":").strip()
            if text:
                await self.mq.outgoing_msg(OutgoingMessage(content=text, channel=self.channel, metadata=self._reply_metadata))

    async def _on_tool_start(self, tool_name: str, tool_args: dict) -> None:
        if self.mq:
            first_arg = str(next(iter(tool_args.values()), ""))[:50] if tool_args else ""
            status = f"running {tool_name} [{first_arg}]..."
            await self.mq.outgoing_msg(OutgoingMessage(content=status, channel=self.channel, metadata=self._reply_metadata))

    async def _on_response(self, content: str | None) -> None:
        if self.mq and content and content.strip():
            await self.mq.outgoing_msg(OutgoingMessage(content=content.strip(), channel=self.channel, metadata=self._reply_metadata))

    async def _on_no_choices(self) -> None:
        self._empty_retries += 1
        if self._empty_retries > _MAX_EMPTY_RETRIES:
            raise RuntimeError(f"No choices in API response after {_MAX_EMPTY_RETRIES} retries")
        wait = min(2 ** self._empty_retries, 60)
        log.warning(f"No choices in API response, retrying in {wait}s (attempt {self._empty_retries}/{_MAX_EMPTY_RETRIES})")
        await asyncio.sleep(wait)

    def _should_stop(self) -> bool:
        return self.channel.has_stopped

    async def process_incoming(self) -> None:
        log.info("BackgroundAgent started processing incoming messages...")
        while True:
            msg = await self.mq.incoming.get()
            try:
                await self.agent_loop(msg.content, msg.metadata)
            except Exception as e:
                log.error(f"Agent loop error: {e}")

    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        self._trim_messages()
        self._empty_retries = 0
        self._reply_metadata = metadata or {}
        self.history.add_message("user", message)

        system_context = load_system_context()
        system = [{"role": "system", "content": system_context}] if system_context else []
        session_messages = system + self.messages[:] + [{"role": "user", "content": message}]

        final_content = await self._loop(session_messages, all_tool_specs)

        self.channel.clear_stopped()

        if len(session_messages) >= 2 and final_content is not None:
            self.messages.append({"role": "user", "content": message})
            self.messages.append(session_messages[-1])
            self.history.add_message("assistant", final_content)

        return final_content
