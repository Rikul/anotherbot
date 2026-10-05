"""``read_file`` tool: read a file, optionally a byte range."""

from ..core.tool import Tool
from ..infra.app_logging import log


class ReadFileTool(Tool):
    """Read a file as UTF-8 (bad bytes replaced), optionally from ``offset`` for ``size`` bytes."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read and return the contents of a file. Supports optional offset and size "
                    "parameters for partial reads."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "The path to the file to read",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Byte offset to start reading from (default: 0)",
                        },
                        "size": {
                            "type": "integer",
                            "description": (
                                "Maximum number of bytes to read (default: read to end of file)"
                            ),
                        },
                    },
                    "required": ["file_path"],
                },
            },
        }

    @staticmethod
    def call(file_path: str, offset: int = 0, size: int | None = None) -> str:
        """Return the file's contents (or the requested byte range); errors as text."""
        log.info(
            "read_file, file_path: %s, offset: %s, size: %s", file_path, offset, size
        )

        if offset < 0:
            return "Error: offset must be non-negative"
        if size is not None and size < 0:
            return "Error: size must be non-negative"

        try:
            with open(file_path, "rb") as f:
                if offset:
                    f.seek(offset)
                data = f.read(size) if size is not None else f.read()
            return data.decode("utf-8", errors="replace")
        except FileNotFoundError:
            return f"Error: file {file_path} does not exist"
        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error reading file %s: %s", file_path, e)
            return f"Error reading file {file_path}: {e}"
