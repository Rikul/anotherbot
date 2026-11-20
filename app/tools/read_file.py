"""``read_file`` tool: read a file, optionally a byte range."""

import os
import stat

from ..core.tool import Tool
from ..infra.app_logging import log

# At most this much is read per call (the result is cut to
# MAX_TOOL_RESULT_LENGTH characters anyway); use offset/size for the rest.
MAX_READ_BYTES = 1024 * 1024


class ReadFileTool(Tool):
    """Read a file as UTF-8 (bad bytes replaced), optionally from ``offset`` for ``size`` bytes."""

    read_only = True

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
            # Regular files only: opening a FIFO blocks until a writer comes,
            # and a device such as /dev/zero never ends.
            if not stat.S_ISREG(os.stat(file_path).st_mode):
                return f"Error: {file_path} is not a regular file"
            limit = MAX_READ_BYTES if size is None else min(size, MAX_READ_BYTES)
            with open(file_path, "rb") as f:
                if offset:
                    f.seek(offset)
                data = f.read(limit + 1)
            more = len(data) > limit and (size is None or size > limit)
            text = data[:limit].decode("utf-8", errors="replace")
            if more:
                text += (
                    f"\n… stopped after {limit} bytes; read further with offset "
                    f"{offset + limit}"
                )
            return text
        except FileNotFoundError:
            return f"Error: file {file_path} does not exist"
        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error reading file %s: %s", file_path, e)
            return f"Error reading file {file_path}: {e}"
