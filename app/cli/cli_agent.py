"""Agent for the terminal: prints output and asks before running tools."""

from __future__ import annotations

import asyncio

from ..channels.channel import ChannelType
from ..core import runtime
from ..core.agent import Agent, MAX_CONTEXT_MESSAGES, get_default_sys_prompt
from ..core.tool_calls import get_all_tool_specs
from ..infra import tracer
from ..infra.conversations import ConversationStore
from ..infra.message_history import MessageHistory
from ..infra.term_display import ANSI


def ask_permission(tool_name: str, args: dict) -> bool:
    """Ask on the terminal whether a tool call may run; Enter means yes."""
    print(
        f"{ANSI.YELLOW}⚡ Tool Call{ANSI.RESET}: {ANSI.CYAN}{tool_name}{ANSI.RESET}"
        f"   Args: {args}"
    )
    print(f"Proceed? {ANSI.DIM}[Y/n]{ANSI.RESET} ", end="", flush=True)
    answer = input().strip().lower()
    return answer in ("", "y", "yes")


class CliAgent(Agent):
    """Terminal agent: prints thinking text and replies, and asks before each tool call.

    Args:
        auto_approve: run tools without asking.
    """

    def __init__(self, max_iterations: int = 250, auto_approve: bool = False) -> None:
        super().__init__(max_iterations)
        self.auto_approve = auto_approve
        self.channel_str = ChannelType.CLI.value

        self.store = ConversationStore()
        self.history = MessageHistory(channel_type=self.channel_str)

        conv = self.store.get_last(self.channel_str)
        if conv is None:
            cid = self.store.create(self.channel_str)
            conv = self.store.get(cid)

        self.conversation_id: int = conv["id"]
        runtime.set("conversation_id", conv["id"])
        runtime.set("conversation_name", conv["name"])
        self.messages.extend(
            self.store.load_messages(self.conversation_id, limit=MAX_CONTEXT_MESSAGES)
        )

    def switch_conversation(self, conv: dict) -> None:
        """Make ``conv`` the active conversation and load its messages."""
        self.conversation_id = conv["id"]
        self.messages = self.store.load_messages(conv["id"], limit=MAX_CONTEXT_MESSAGES)
        runtime.set("conversation_id", conv["id"])
        runtime.set("conversation_name", conv["name"])

    async def _on_thinking(self, content: str | None) -> None:
        if content and content.strip():
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
        attachments = self._as_list((metadata or {}).get("files"))
        user_msg = self._build_user_message(message, metadata)
        placeholder_content = self._build_placeholder_content(message, attachments)
        self.history.add_message("user", placeholder_content, self.conversation_id)

        conv = self.store.get(self.conversation_id)
        system_context = get_default_sys_prompt(
            {
                "channel": self.channel_str,
                "conversation_id": self.conversation_id,
                "conversation_name": conv["name"] if conv else "New Conversation",
            }
        )
        system = (
            [{"role": "system", "content": system_context}] if system_context else []
        )
        session_messages = system + self.messages[:] + [user_msg]

        final_content = await self._loop(session_messages, get_all_tool_specs())

        if runtime.get("trace"):
            tracer.write_trace(session_messages)

        self.messages.append({"role": "user", "content": placeholder_content})
        self.messages.append({"role": "assistant", "content": final_content})
        self.history.add_message("assistant", final_content, self.conversation_id)
        self.store.touch(self.conversation_id)

        if self.store.count_user_messages(self.conversation_id) == 1:
            conv = self.store.get(self.conversation_id)
            if conv and conv["name"] == "New Conversation":
                asyncio.create_task(
                    self._auto_name(
                        self.store,
                        self.conversation_id,
                        list(self.messages),
                        "conversation_name",
                    )
                )

        return final_content
