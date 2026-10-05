import os
from ..infra.app_logging import log
from pathlib import Path
from ..core.tool import Tool


class WriteFileTool(Tool):
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
        log.info(f"write_file, file_path: {file_path}")

        try:
            os.makedirs(Path(file_path).parent, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            log.error(f"Error writing to file {file_path}: {e}")
            return f"Error writing to file {file_path}: {e}"

        return f"Successfully wrote to file: {file_path}"
