from __future__ import annotations

from ..core.tool_calls import all_tool_specs
from .cli import ask_permission
from ..core.agent import Agent, MAX_CONTEXT_MESSAGES
from ..infra.message_history import MessageHistory
from ..channels.channel import ChannelType
from ..infra.startup import load_system_context


class CliAgent(Agent):

    def __init__(self, max_iterations: int = 250, auto_approve: bool = False, silent: bool = False) -> None:
        super().__init__(max_iterations)
        self.auto_approve = auto_approve or silent
        self.silent = silent
        self.history = MessageHistory(channel_type=ChannelType.CLI.value)
        self.messages.extend(self.history.get_history(limit=MAX_CONTEXT_MESSAGES))

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
        self.history.add_message("user", message)

        system_context = load_system_context()
        system = [{"role": "system", "content": system_context}] if system_context else []
        session_messages = system + self.messages[:] + [{"role": "user", "content": message}]

        final_content = await self._loop(session_messages, all_tool_specs)

        if len(session_messages) >= 2:
            self.messages.append({"role": "user", "content": message})
            self.messages.append(session_messages[-1])
            self.history.add_message("assistant", final_content)

        return final_content
