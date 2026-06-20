from ..core.tool import Tool
from ..infra.app_logging import log

class HelperAgentTool(Tool):

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "helper_agent",
                "description": "Run a helper agent to perform a task or research. Use this to delegate sub-tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The prompt or task instructions for the helper agent."
                        },
                        "system_prompt": {
                            "type": "string",
                            "description": "Optional system instructions to define the role, guidelines, or constraints for the helper agent."
                        }
                    },
                    "required": ["prompt"]
                }
            }
        }

    @staticmethod
    async def call(prompt: str, system_prompt: str = None) -> str:
        log.info(f"helper_agent tool called, prompt length: {len(prompt)}")
        from ..core.helper_agent import HelperAgent
        agent = HelperAgent(system_prompt=system_prompt)
        return await agent.run(prompt)
