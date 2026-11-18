"""Agent without a channel, used for scheduled tasks, sub-tasks and auto-naming."""

from __future__ import annotations

from ..infra.app_logging import log
from .agent import Agent


class HelperAgent(Agent):
    """A one-off agent with no user, history or conversation.

    It only gets the ``helper_tool_specs`` allowlist (no scheduled-task
    mutation tools, no MCP tools) to prevent recursion.
    """

    trace_label = "helper"

    def __init__(self, system_prompt: str = None, max_iterations: int = 50) -> None:
        super().__init__(max_iterations)
        if system_prompt:
            self.messages.insert(0, {"role": "system", "content": system_prompt})

    async def run(self, prompt: str) -> str:
        """Run ``prompt`` to completion and return the final answer."""
        log.info("Running HelperAgent with prompt: %s", prompt)
        return await self.agent_loop(prompt)

    async def handle_tool_call(self, tool_call) -> str:
        # The model only sees helper_tool_specs, but refuse anything else it
        # names anyway (e.g. save_memory, add_scheduled_task).
        from .tool_calls import helper_tool_specs  # pylint: disable=import-outside-toplevel

        name = tool_call.function.name
        if name not in {s["function"]["name"] for s in helper_tool_specs}:
            log.warning("HelperAgent refused off-list tool call: %s", name)
            return f"Tool {name} is not available to the helper agent."
        return await super().handle_tool_call(tool_call)

    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        # Lazy: tool_calls -> scheduled_tasks -> helper_agent (circular import).
        from .tool_calls import helper_tool_specs  # pylint: disable=import-outside-toplevel

        self.messages.append(self._build_user_message(message, metadata))
        self.tracer.start_turn(self.messages)
        try:
            return await self._loop(self.messages, helper_tool_specs)
        finally:
            self.tracer.close()
