from __future__ import annotations

from ..infra.app_logging import log
from .agent import Agent


class HelperAgent(Agent):

    def __init__(self, system_prompt: str = None, max_iterations: int = 50) -> None:
        super().__init__(max_iterations)
        if system_prompt:
            self.messages.insert(0, {"role": "system", "content": system_prompt})

    async def run(self, prompt: str) -> str:
        log.info(f"Running HelperAgent with prompt: {prompt}")
        return await self.agent_loop(prompt)

    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        from .tool_calls import helper_tool_specs  # lazy — avoids circular import via scheduled_tasks
        self.messages.append({"role": "user", "content": message})
        return await self._loop(self.messages, helper_tool_specs)
