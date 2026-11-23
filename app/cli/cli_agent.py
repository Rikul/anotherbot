"""Agent for the terminal: prints output and asks before running tools."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from ..channels.channel import ChannelType
from ..core import runtime
from ..core.agent import Agent, MAX_CONTEXT_MESSAGES, get_default_sys_prompt
from ..core.tool_calls import get_all_tool_specs, is_read_only
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


INTERRUPTED_REPLY = "[Interrupted by user]"


class CliUI(Protocol):
    """What the terminal UI (``app/cli/tui.py``) provides to ``CliAgent``."""

    def thinking(self, text: str) -> None:
        """Show text the model wrote alongside tool calls."""

    def tool_start(self, name: str, args: dict) -> None:
        """Show that a tool is about to run."""

    def tool_result(self, name: str, result: str) -> None:
        """Show a short summary of a tool's result."""

    def response(self, text: str) -> None:
        """Show the turn's final reply."""

    async def ask_permission(self, name: str, args: dict) -> bool:
        """Ask whether a tool call may run."""


class CliAgent(Agent):
    """Terminal agent: prints thinking text and replies, and asks before each tool call.

    Args:
        auto_approve: run tools without asking.
        ui: the interactive TUI; without one, output is plain ``print()`` and
            permission prompts read stdin (single-prompt mode, pipes).
    """

    trace_label = "cli"
    saves_trajectories = True
    ui: CliUI | None = None

    def __init__(
        self,
        max_iterations: int = 250,
        auto_approve: bool = False,
        ui: CliUI | None = None,
    ) -> None:
        super().__init__(max_iterations)
        self.auto_approve = auto_approve
        self.ui = ui
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
            if self.ui:
                self.ui.thinking(content.strip())
            else:
                print(content.strip())

    async def _check_permission(self, tool_name: str, tool_args: dict) -> bool:
        if self.auto_approve or is_read_only(tool_name):
            return True
        if self.ui:
            return await self.ui.ask_permission(tool_name, tool_args)
        return ask_permission(tool_name, tool_args)

    async def _on_tool_start(self, tool_name: str, tool_args: dict) -> None:
        if self.ui:
            self.ui.tool_start(tool_name, tool_args)

    async def handle_tool_call(self, tool_call) -> str:
        result = await super().handle_tool_call(tool_call)
        if self.ui:
            self.ui.tool_result(tool_call.function.name, result)
        return result

    async def _on_response(self, content: str | None) -> None:
        if content and content.strip():
            if self.ui:
                self.ui.response(content)
            else:
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
        turn_start = len(session_messages) - 1
        started_at = datetime.now()
        self.tracer.start_turn(session_messages, self.conversation_id)
        if self.tracer.enabled() and self.tracer.path:
            runtime.set("last_trace", self.tracer.path.name)

        try:
            final_content = await self._loop(session_messages, get_all_tool_specs())
        except asyncio.CancelledError:
            # Ctrl+C in the TUI: keep the stored history and the in-memory
            # context consistent (the user message is already saved).
            self.messages.append({"role": "user", "content": placeholder_content})
            self.messages.append({"role": "assistant", "content": INTERRUPTED_REPLY})
            self.history.add_message(
                "assistant", INTERRUPTED_REPLY, self.conversation_id
            )
            raise

        self.messages.append({"role": "user", "content": placeholder_content})
        self.messages.append({"role": "assistant", "content": final_content})
        message_id = self.history.add_message(
            "assistant", final_content, self.conversation_id
        )
        self._save_trajectory(
            store=self.store,
            message_id=message_id,
            conversation_id=self.conversation_id,
            channel=self.channel_str,
            session_messages=session_messages,
            turn_start=turn_start,
            started_at=started_at,
        )
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
