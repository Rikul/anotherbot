from __future__ import annotations

import asyncio

from .tool_calls import all_tool_specs
from ..infra.app_logging import log
from ..channels.channel import Channel
from ..channels.message import OutgoingMessage
from ..channels.message_queue import MessageQueue
from .agent import Agent, MAX_CONTEXT_MESSAGES, get_default_sys_prompt
from ..infra.message_history import MessageHistory
from ..infra.conversations import ConversationStore
from . import runtime

_MAX_EMPTY_RETRIES = 5


class BackgroundAgent(Agent):

    def __init__(self, mq: MessageQueue = None, channel: Channel = None, max_iterations: int = 250) -> None:
        super().__init__(max_iterations)
        self.mq = mq
        self.channel = channel

        if self.channel is None:
            raise ValueError("channel must be specified for BackgroundAgent")

        self._channel_str = channel.channel_type.value
        self._store = ConversationStore()
        self.history = MessageHistory(channel_type=self._channel_str)

        conv = self._store.get_last(self._channel_str)
        if conv is None:
            cid = self._store.create(self._channel_str)
            conv = self._store.get(cid)

        self.conversation_id: int = conv["id"]
        runtime.set(f"conversation_id:{self._channel_str}", conv["id"])
        runtime.set(f"conversation_name:{self._channel_str}", conv["name"])
        self.messages.extend(self._store.load_messages(self.conversation_id, limit=MAX_CONTEXT_MESSAGES))

        self._reply_metadata: dict = {}
        self._empty_retries: int = 0

        # Lazy import to avoid circular: commands imports runtime which is fine,
        # but conversation commands need self reference so we build registry here.
        from ..channels.commands import (
            CommandRegistry, BotCommand, help_cmd,
            list_conversations_cmd, new_conversation_cmd, load_conversation_cmd,
            fork_conversation_cmd, rename_conversation_cmd, export_conversation_cmd,
        )
        self.registry = CommandRegistry()
        ch = self._channel_str
        self.registry.register(BotCommand("list-conversations",  "List conversations.", list_conversations_cmd(self._store, ch)))
        self.registry.register(BotCommand("new-conversation",    "Start a new conversation.", new_conversation_cmd(self)))
        self.registry.register(BotCommand("load-conversation",   "Load a conversation. Usage: /load-conversation <id>", load_conversation_cmd(self)))
        self.registry.register(BotCommand("fork-conversation",   "Fork a conversation. Usage: /fork-conversation [id]", fork_conversation_cmd(self)))
        self.registry.register(BotCommand("rename-conversation", "Rename a conversation. Usage: /rename-conversation <id> <name>", rename_conversation_cmd(self._store, ch)))
        self.registry.register(BotCommand("export-conversation", "Export a conversation to JSON. Usage: /export-conversation [id]", export_conversation_cmd(self._store, ch)))
        self.registry.register(BotCommand("help", "Show available commands.", help_cmd(self.registry)))

    def _switch_conversation(self, conv: dict) -> None:
        self.conversation_id = conv["id"]
        self.messages = self._store.load_messages(conv["id"], limit=MAX_CONTEXT_MESSAGES)
        runtime.set(f"conversation_id:{self._channel_str}", conv["id"])
        runtime.set(f"conversation_name:{self._channel_str}", conv["name"])

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
                if msg.content.startswith("/"):
                    parts = msg.content[1:].split(maxsplit=1)
                    cmd_name = parts[0].lower()
                    args = parts[1] if len(parts) > 1 else ""
                    result = await self.registry.execute(cmd_name, args)
                    reply = result if result is not None else f"Unknown command: /{cmd_name}"
                    await self.mq.outgoing_msg(OutgoingMessage(
                        content=reply, channel=self.channel, metadata=msg.metadata
                    ))
                else:
                    await self.agent_loop(msg.content, msg.metadata)
            except Exception as e:
                log.error(f"Agent loop error: {e}")
                await self.mq.outgoing_msg(OutgoingMessage(content=str(e), channel=self.channel, metadata=msg.metadata))

    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        self._trim_messages()
        self._empty_retries = 0
        self._reply_metadata = metadata or {}
        self.history.add_message("user", message, self.conversation_id)

        conv = self._store.get(self.conversation_id)
        system_context = get_default_sys_prompt({
            "channel": self._channel_str,
            "conversation_id": self.conversation_id,
            "conversation_name": conv["name"] if conv else "New Conversation",
        })
        system = [{"role": "system", "content": system_context}] if system_context else []
        session_messages = system + self.messages[:] + [{"role": "user", "content": message}]

        final_content = await self._loop(session_messages, all_tool_specs)

        self.channel.clear_stopped()

        self.messages.append({"role": "user", "content": message})
        self.messages.append({"role": "assistant", "content": final_content})
        self.history.add_message("assistant", final_content, self.conversation_id)
        self._store.touch(self.conversation_id)

        if self._store.count_user_messages(self.conversation_id) == 1:
            conv = self._store.get(self.conversation_id)
            if conv and conv["name"] == "New Conversation":
                asyncio.create_task(
                    self._auto_name(
                        self._store, self.conversation_id, list(self.messages),
                        f"conversation_name:{self._channel_str}",
                    )
                )

        return final_content
