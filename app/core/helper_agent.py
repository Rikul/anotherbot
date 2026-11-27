"""Agent without a channel, used for scheduled tasks, sub-tasks and auto-naming."""

from __future__ import annotations

from ..infra.app_logging import log
from .agent import Agent


class HelperAgent(Agent):
    """A one-off agent with no user, history or conversation.

    It gets only the tools of its ``toolsets`` (``toolsets.TOOLSETS``); the
    default, ``helper``, has no scheduled-task mutation, delegation or MCP
    tools, to prevent recursion. ``Agent.handle_tool_call`` refuses any
    other tool the model names.
    """

    trace_label = "helper"

    def __init__(
        self,
        system_prompt: str = None,
        max_iterations: int = 50,
        toolsets: tuple[str, ...] = ("helper",),
    ) -> None:
        super().__init__(max_iterations)
        self.set_toolsets(toolsets)
        # Restricted from the start, not only once a turn has begun.
        self._allowed_tools = {s["function"]["name"] for s in self.tool_specs()}
        if system_prompt:
            self.messages.insert(0, {"role": "system", "content": system_prompt})

    async def run(self, prompt: str) -> str:
        """Run ``prompt`` to completion and return the final answer."""
        log.info("Running HelperAgent with prompt: %s", prompt)
        return await self.agent_loop(prompt)

    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        self.messages.append(self._build_user_message(message, metadata))
        self.tracer.start_turn(self.messages)
        try:
            return await self._loop(self.messages, self.tool_specs())
        finally:
            self.tracer.close()
