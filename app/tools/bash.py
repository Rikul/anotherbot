"""``bash`` tool: run a shell command."""

import subprocess

from ..core.tool import Tool
from ..infra.app_logging import log


class BashTool(Tool):
    """Run a shell command (with timeout); return stdout plus any stderr under ``[stderr]``."""

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
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds; default 30, min 1 max 300",
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(command: str, timeout: int = 30) -> str:
        """Run ``command`` in a shell; errors (including timeouts) are returned as text."""
        log.info("bash, command: %s", command)

        try:
            timeout = int(timeout) or 30
            timeout = max(1, min(timeout, 300))

            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            return output

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error executing command '%s': %s", command, e)
            return f"Error executing command: {e}"
