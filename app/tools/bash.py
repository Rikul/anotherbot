import subprocess
from ..infra.app_logging import log
from ..core.tool import Tool


class BashTool(Tool):
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "required": ["command"],
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The command to execute",
                        }
                    },
                },
            },
        }

    @staticmethod
    def call(command: str) -> str:
        log.info(f"bash, command: {command}")

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            return output

        except Exception as e:
            log.error(f"Error executing command '{command}': {e}")
            return f"Error executing command: {e}"
