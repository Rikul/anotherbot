from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from ..config import APP_NAME
from .term_display import ANSI

LOG_DIR = Path.home() / f".{APP_NAME}" / "logs"

class AnsiFormatter(logging.Formatter):
    LEVELS = {
        logging.DEBUG:    f"{ANSI.DIM}DEBUG{ANSI.RESET}",
        logging.INFO:     f"{ANSI.GREEN}INFO{ANSI.RESET}",
        logging.WARNING:  f"{ANSI.YELLOW}WARN{ANSI.RESET}",
        logging.ERROR:    f"{ANSI.RED}ERROR{ANSI.RESET}",
        logging.CRITICAL: f"{ANSI.BOLD_RED}CRIT{ANSI.RESET}",
    }

    def format(self, record: logging.LogRecord) -> str:
        level = self.LEVELS.get(record.levelno, record.levelname)
        msg = record.getMessage()
        return f"{ANSI.DIM}{self.formatTime(record, '%H:%M:%S')}{ANSI.RESET} {level}" \
               f" {ANSI.DIM}{record.name} {msg}{ANSI.RESET}"


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return (
            f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')}"
            f" {record.levelname} {record.name} {record.getMessage()}"
        )
    

def setup_logging(level: int = logging.INFO):
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # console handler — ANSI colors
    console = logging.StreamHandler()
    console.setFormatter(AnsiFormatter())

    # file handler — plain text, rotates at 5MB, keeps 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(PlainFormatter())

    logging.basicConfig(level=level, handlers=[console, file_handler])

    # silence noisy libraries
    for lib in ("httpx", "httpcore", "openai", "LiteLLM", "litellm", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)


log = logging.getLogger(__name__)

