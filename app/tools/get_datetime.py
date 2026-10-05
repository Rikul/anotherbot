"""``get_datetime`` tool: current local date and time."""

from datetime import datetime

from ..core.tool import Tool
from ..infra.app_logging import log


class GetDateTime(Tool):
    """Return the current local date and time."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "get_datetime",
                "description": "Get current date and time in ISO format with timezone",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def call() -> str:
        """Return ``YYYY-MM-DD HH:MM:SS`` plus the local time zone name."""
        log.info("get_datetime")

        now = datetime.now()
        return f"{now.strftime('%Y-%m-%d %H:%M:%S')} {now.astimezone().tzname()}"
