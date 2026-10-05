"""``helper_agent`` tool: delegate a sub-task to a fresh helper agent."""

from ..core.tool import Tool
from ..infra.app_logging import log


class HelperAgentTool(Tool):
    """Run a prompt in a separate ``HelperAgent`` (optionally with its own system prompt)."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "helper_agent",
                "description": (
                    "Run a helper agent to perform a task or research. Use this to delegate "
                    "sub-tasks."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The prompt or task instructions for the helper agent.",
                        },
                        "system_prompt": {
                            "type": "string",
                            "description": (
                                "Optional system instructions to define the role, guidelines, or "
                                "constraints for the helper agent."
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
            },
        }

    @staticmethod
    async def call(prompt: str, system_prompt: str = None) -> str:
        """Run ``prompt`` in a new ``HelperAgent`` and return its final answer."""
        log.info("helper_agent tool called with prompt: %s", prompt)
        # Lazy: core.helper_agent -> core.agent -> tool_calls -> this module.
        from ..core.helper_agent import HelperAgent  # pylint: disable=import-outside-toplevel

        agent = HelperAgent(system_prompt=system_prompt)
        return await agent.run(prompt)
