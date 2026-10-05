from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

_config: dict = {}

# Load .env before anything reads os.environ: the nearest .env (searched from
# app/ upwards, e.g. the repo root) may set ANOTHERBOT_HOME, which decides
# PROJECT_HOME; $PROJECT_HOME/.env is then loaded too. Neither overrides vars
# already set in the real environment.
load_dotenv()

APP_NAME = "anotherbot"
PROJECT_HOME = Path(os.environ.get("ANOTHERBOT_HOME", Path.home() / f".{APP_NAME}"))
APP_DB = PROJECT_HOME / "app.db"

load_dotenv(PROJECT_HOME / ".env")


def get_db_connection(
    db_path: Path = APP_DB, *, timeout: float = 30.0, isolation_level: str | None = ""
) -> sqlite3.Connection:
    """Open a connection to a shared SQLite db (e.g. APP_DB) with settings safe
    for concurrent access from multiple asyncio tasks/channels.

    The timeout is passed straight to sqlite3.connect, which sets the
    connection's busy timeout so writers retry instead of immediately raising
    "database is locked" when another connection briefly holds the write lock.
    WAL mode (readers don't block the writer) is persisted in the db file
    itself, so it only needs to be enabled once by each store's schema-init
    step rather than on every connection.
    """
    return sqlite3.connect(db_path, timeout=timeout, isolation_level=isolation_level)


def load() -> None:
    global _config

    _config = {
        "model": "deepseek/deepseek-v4.1-flash",
        "base_url": "https://openrouter.ai/api/v1",
        "max_iterations": 250,
        "telegram": {},
    }

    if v := os.environ.get("TELEGRAM_BOT_TOKEN"):
        _config.setdefault("telegram", {})["BOT_TOKEN"] = v
    if v := os.environ.get("TELEGRAM_ALLOW_FROM"):
        _config.setdefault("telegram", {})["ALLOW_FROM"] = [
            int(x.strip()) for x in v.split(",") if x.strip()
        ]
    if v := os.environ.get("DISCORD_BOT_TOKEN"):
        _config.setdefault("discord", {})["TOKEN"] = v
    if v := os.environ.get("DISCORD_ALLOW_FROM"):
        _config.setdefault("discord", {})["ALLOW_FROM"] = [
            int(x.strip()) for x in v.split(",") if x.strip()
        ]
    if v := os.environ.get("WEBSOCKET_HOST"):
        _config.setdefault("websocket", {})["HOST"] = v
    if v := os.environ.get("WEBSOCKET_PORT"):
        try:
            _config.setdefault("websocket", {})["PORT"] = int(v)
        except ValueError:
            raise ValueError(f"WEBSOCKET_PORT must be an integer, got: {v!r}") from None

    if v := os.environ.get("MAX_ITERATIONS"):
        try:
            _config["max_iterations"] = int(v)
        except ValueError:
            raise ValueError(f"MAX_ITERATIONS must be an integer, got: {v!r}") from None

    if v := os.environ.get("WEB_PASSWORD"):
        _config["web_password"] = v

    if v := os.environ.get("LLM_BASE_URL"):
        _config["base_url"] = v

    if v := os.environ.get("LLM_MODEL"):
        _config["model"] = v

    if v := os.environ.get("LLM_API_KEY"):
        _config["api_key"] = v


def get(key: str, default=None):
    return _config.get(key, default)


def set(key: str, value) -> None:
    _config[key] = value


def __getattr__(name: str):
    if name in _config:
        return _config[name]
    raise AttributeError(f"Config has no attribute {name}")
