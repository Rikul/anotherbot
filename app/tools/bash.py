import re
import subprocess
from ..infra.app_logging import log
from ..core.tool import Tool


_LOG_COMMAND_MAX_LENGTH = 200
_REDACTED = "[REDACTED]"
_SECRET_ENV_NAME_PARTS = ("token", "secret", "password", "passwd", "api_key", "apikey", "access_key")
_ENV_ASSIGNMENT_RE = re.compile(r"((?:^|\s)(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(=)(\"[^\"]*\"|'[^']*'|\S+)")
_SECRET_PATTERNS = (
    re.compile(r"((?:authorization|x-api-key|api-key|token|password|secret)\s*:\s*(?:bearer\s+)?)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"([?&](?:token|access_token|api[_-]?key|password|secret)=)[^&\s'\"]+", re.IGNORECASE),
    re.compile(r"([a-z][a-z0-9+.-]*://)([^/@:\s]+):([^@/\s]+)@", re.IGNORECASE),
)


def _redact_env_assignment(match: re.Match[str]) -> str:
    prefix, name, equals, value = match.groups()
    normalized = name.lower().replace("-", "_")
    if any(part in normalized for part in _SECRET_ENV_NAME_PARTS):
        return f"{prefix}{name}{equals}{_REDACTED}"
    return f"{prefix}{name}{equals}{value}"


def _summarize_command_for_logs(command: str, max_length: int = _LOG_COMMAND_MAX_LENGTH) -> str:
    """Return a redacted, bounded command summary for application logs."""
    summary = _ENV_ASSIGNMENT_RE.sub(_redact_env_assignment, command)
    summary = _SECRET_PATTERNS[0].sub(rf"\1{_REDACTED}", summary)
    summary = _SECRET_PATTERNS[1].sub(rf"\1{_REDACTED}", summary)
    summary = _SECRET_PATTERNS[2].sub(rf"\1{_REDACTED}@", summary)
    if len(summary) > max_length:
        return summary[: max_length - 3] + "..."
    return summary


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
                            "description": "The command to execute"
                        }
                    }
                }
            }
        }

    @staticmethod
    def call(command: str) -> str:
        command_summary = _summarize_command_for_logs(command)
        log.info(f"bash, command: {command_summary}")

        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=False, timeout=30)
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            return output

        except Exception as e:
            log.error(f"Error executing command '{command_summary}': {e}")
            return f"Error executing command: {e}"
