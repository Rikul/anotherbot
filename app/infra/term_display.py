"""ANSI escape codes for coloured terminal output."""

from __future__ import annotations

from enum import StrEnum


class ANSI(StrEnum):
    """ANSI SGR escape sequences; members are plain strings usable in f-strings."""

    RESET = "\033[0m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    BOLD_RED = "\033[1;31m"
