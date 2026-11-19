"""``bash`` tool: run a shell command."""

import asyncio
import os
import signal

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
    async def call(command: str, timeout: int = 30) -> str:
        """Run ``command`` in a shell; errors (including timeouts) are returned as text.

        The shell gets its own process group, which is killed on timeout and
        when the caller is cancelled (e.g. Ctrl+C in the TUI cancels the turn),
        so a command never outlives the turn that started it.
        """
        log.info("bash, command: %s", command)

        try:
            timeout = int(timeout) or 30
            timeout = max(1, min(timeout, 300))
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error executing command '%s': %s", command, e)
            return f"Error executing command: {e}"

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            await _kill(proc)
            return f"Error executing command: timed out after {timeout} seconds"
        except asyncio.CancelledError:
            await _kill(proc)
            raise

        output = stdout.decode(errors="replace")
        if stderr:
            output += f"\n[stderr]\n{stderr.decode(errors='replace')}"
        return output


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill the shell and everything it started (its process group)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except (TimeoutError, asyncio.CancelledError):
        pass
