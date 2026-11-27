"""Agent without a channel, used for scheduled tasks, sub-tasks and auto-naming."""

from __future__ import annotations

from ..infra.app_logging import log
from .agent import Agent


class HelperAgent(Agent):
    """A one-off agent with no user, history or conversation.

    It gets only the tools of its ``toolsets`` (``toolsets.TOOLSETS``); the
    default, ``helper``, has no scheduled-task mutation, delegation or MCP
    tools, to prevent recursion.
    """

    trace_label = "helper"

    def __init__(
        self,
        system_prompt: str = None,
        max_iterations: int = 50,
        toolsets: tuple[str, ...] = ("helper",),
    ) -> None:
        super().__init__(max_iterations)
        # Lazy: tool_calls -> scheduled_tasks -> helper_agent (circular import).
        from .tool_calls import toolset_specs  # pylint: disable=import-outside-toplevel

        self.tool_specs = toolset_specs(*toolsets)
        self._allowed_tools = {s["function"]["name"] for s in self.tool_specs}
        if system_prompt:
            self.messages.insert(0, {"role": "system", "content": system_prompt})

    async def run(self, prompt: str) -> str:
        """Run ``prompt`` to completion and return the final answer."""
        log.info("Running HelperAgent with prompt: %s", prompt)
        return await self.agent_loop(prompt)

    async def handle_tool_call(self, tool_call) -> str:
        # The model only sees self.tool_specs, but refuse anything else it
        # names anyway (e.g. save_memory, add_scheduled_task).
        name = tool_call.function.name
        if name not in self._allowed_tools:
            log.warning("HelperAgent refused off-list tool call: %s", name)
            return f"Tool {name} is not available to the helper agent."
        return await super().handle_tool_call(tool_call)

    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        self.messages.append(self._build_user_message(message, metadata))
        self.tracer.start_turn(self.messages)
        try:
            return await self._loop(self.messages, self.tool_specs)
        finally:
            self.tracer.close()
