from __future__ import annotations

import asyncio

from ..core.tool_calls import all_tool_specs
from .cli import ask_permission
from ..core.agent import Agent, MAX_CONTEXT_MESSAGES, get_default_sys_prompt
from ..core import runtime
from ..infra.message_history import MessageHistory
from ..infra.conversations import ConversationStore
from ..channels.channel import ChannelType


class CliAgent(Agent):

    def __init__(self, max_iterations: int = 250, auto_approve: bool = False, silent: bool = False) -> None:
        super().__init__(max_iterations)
        self.auto_approve = auto_approve or silent
        self.silent = silent
        self._channel_str = ChannelType.CLI.value

        self._store = ConversationStore()
        self.history = MessageHistory(channel_type=self._channel_str)

        conv = self._store.get_last(self._channel_str)
        if conv is None:
            cid = self._store.create(self._channel_str)
            conv = self._store.get(cid)

        self.conversation_id: int = conv["id"]
        runtime.set("conversation_id", conv["id"])
        runtime.set("conversation_name", conv["name"])
        self.messages.extend(self._store.load_messages(self.conversation_id, limit=MAX_CONTEXT_MESSAGES))

    def _switch_conversation(self, conv: dict) -> None:
        self.conversation_id = conv["id"]
        self.messages = self._store.load_messages(conv["id"], limit=MAX_CONTEXT_MESSAGES)
        runtime.set("conversation_id", conv["id"])
        runtime.set("conversation_name", conv["name"])

    async def _on_thinking(self, content: str | None) -> None:
        if not self.silent and content and content.strip():
            print(content.strip())

    async def _check_permission(self, tool_name: str, tool_args: dict) -> bool:
        if self.auto_approve:
            return True
        return ask_permission(tool_name, tool_args)

    async def _on_response(self, content: str | None) -> None:
        if content and content.strip():
            print(content)

    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        self._trim_messages()
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

        self.messages.append({"role": "user", "content": message})
        self.messages.append({"role": "assistant", "content": final_content})
        self.history.add_message("assistant", final_content, self.conversation_id)
        self._store.touch(self.conversation_id)

        if self._store.count_user_messages(self.conversation_id) == 1:
            conv = self._store.get(self.conversation_id)
            if conv and conv["name"] == "New Conversation":
                asyncio.create_task(
                    self._auto_name(self._store, self.conversation_id, list(self.messages), "conversation_name")
                )

        return final_content
