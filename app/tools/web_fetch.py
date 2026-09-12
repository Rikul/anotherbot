import subprocess
from ..infra.app_logging import log
from ..core.tool import Tool


_CURL_CONNECT_TIMEOUT_SECS = 5
_SUBPROCESS_TIMEOUT_SECS = 10


class WebFetchTool(Tool):

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
                            "description": "The URL of the web page to fetch"
                        }
                    },
                    "required": ["url"]
                }
            }
        }

    @staticmethod
    def call(url: str) -> str:
        log.info(f"web_fetch, url: {url}")

        try:
            result = subprocess.run(
                [
                    "curl",
                    "--silent",
                    "--show-error",
                    "--fail",
                    "--location",
                    "--connect-timeout",
                    str(_CURL_CONNECT_TIMEOUT_SECS),
                    url,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SECS
            )
            if result.returncode != 0:
                return f"Error fetching URL {url}: {result.stderr.strip()}"
            return result.stdout.strip()

        except Exception as e:
            log.error(f"Error fetching URL {url}: {str(e)}")
            return f"Error fetching URL {url}: {str(e)}"
