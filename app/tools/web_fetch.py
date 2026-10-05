"""``web_fetch`` tool: fetch a URL with curl."""

import subprocess

from ..core.tool import Tool
from ..infra.app_logging import log


class WebFetchTool(Tool):
    """Fetch a URL with ``curl -sL`` (10 s timeout) and return the body."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "Fetch and return the contents of a web page given its URL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL of the web page to fetch",
                        }
                    },
                    "required": ["url"],
                },
            },
        }

    @staticmethod
    def call(url: str) -> str:
        """Return the body of ``url``, or an error message if curl fails."""
        log.info("web_fetch, url: %s", url)

        try:
            result = subprocess.run(
                ["curl", "-sL", url],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return f"Error fetching URL {url}: {result.stderr.strip()}"
            return result.stdout.strip()

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error fetching URL %s: %s", url, str(e))
            return f"Error fetching URL {url}: {str(e)}"
