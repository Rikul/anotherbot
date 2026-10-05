"""``write_file`` tool: write text to a file."""

import os
from pathlib import Path

from ..core.tool import Tool
from ..infra.app_logging import log


class WriteFileTool(Tool):
    """Write ``content`` to a file, creating parent directories as needed."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file",
                "parameters": {
                    "type": "object",
                    "required": ["file_path", "content"],
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "The path of the file to write to",
                        },
                        "content": {
                            "type": "string",
                            "description": "The content to write to the file",
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(file_path: str, content: str) -> str:
        """Write ``content`` to ``file_path`` (overwriting it); return a status message."""
        log.info("write_file, file_path: %s", file_path)

        try:
            os.makedirs(Path(file_path).parent, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error writing to file %s: %s", file_path, e)
            return f"Error writing to file {file_path}: {e}"

        return f"Successfully wrote to file: {file_path}"
