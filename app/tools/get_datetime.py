from ..infra.app_logging import log
from ..core.tool import Tool

from datetime import datetime

class GetDateTime(Tool):

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "get_datetime",
                "description": "Get current date and time in ISO format with timezone",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        }

    @staticmethod
    def call() -> str:
        log.info("get_datetime")

        now = datetime.now()
        return f"{now.strftime("%Y-%m-%d %H:%M:%S")} {now.astimezone().tzname()}"
    