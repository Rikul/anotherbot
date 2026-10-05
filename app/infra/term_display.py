from __future__ import annotations

from enum import StrEnum


class ANSI(StrEnum):
    RESET = "\033[0m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    BOLD_RED = "\033[1;31m"
