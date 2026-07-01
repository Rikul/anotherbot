from __future__ import annotations

import os
import sqlite3
import tomllib
from pathlib import Path

_config: dict = {}

APP_NAME = "crafterscode"
PROJECT_HOME = Path(os.environ.get("ANOTHERBOT_HOME", Path.home() / f".{APP_NAME}"))
HOME_CONFIG_PATH = PROJECT_HOME / "config.toml"
APP_DB = PROJECT_HOME / "app.db"


def get_db_connection(db_path: Path = APP_DB, *, timeout: float = 30.0,
                       isolation_level: str | None = "") -> sqlite3.Connection:
    """Open a connection to a shared SQLite db (e.g. APP_DB) with settings safe
    for concurrent access from multiple asyncio tasks/channels.

    WAL mode lets readers proceed without blocking the writer, and the longer
    busy_timeout makes writers retry instead of immediately raising
    "database is locked" when another connection briefly holds the write lock.
    """
    conn = sqlite3.connect(db_path, timeout=timeout, isolation_level=isolation_level)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return conn


def load(path: Path | str = HOME_CONFIG_PATH) -> None:
    global _config

    if os.path.exists(path):
        with open(path, "rb") as f:
            _config = tomllib.load(f)
    else:
        _config = {
            "model": "deepseek/deepseek-v4-flash",
            "base_url": "https://openrouter.ai/api/v1",
            "max_iterations": 100,
            "telegram": {},
        }

    # env var overrides — takes precedence over config file (Docker-friendly)
    if v := os.environ.get("MODEL"):
        _config["model"] = v
    if v := os.environ.get("TELEGRAM_BOT_TOKEN"):
        _config.setdefault("telegram", {})["BOT_TOKEN"] = v
    if v := os.environ.get("TELEGRAM_ALLOW_FROM"):
        _config.setdefault("telegram", {})["ALLOW_FROM"] = [int(x.strip()) for x in v.split(",") if x.strip()]
    if v := os.environ.get("DISCORD_BOT_TOKEN"):
        _config.setdefault("discord", {})["TOKEN"] = v
    if v := os.environ.get("DISCORD_ALLOW_FROM"):
        _config.setdefault("discord", {})["ALLOW_FROM"] = [int(x.strip()) for x in v.split(",") if x.strip()]
    if v := os.environ.get("WEBSOCKET_HOST"):
        _config.setdefault("websocket", {})["HOST"] = v
    if v := os.environ.get("WEBSOCKET_PORT"):
        try:
            _config.setdefault("websocket", {})["PORT"] = int(v)
        except ValueError:
            raise ValueError(f"WEBSOCKET_PORT must be an integer, got: {v!r}") from None


def get(key: str, default=None):
    return _config.get(key, default)

def set(key: str, value) -> None:
    _config[key] = value
    
def __getattr__(name: str):
    if name in _config:
        return _config[name]
    raise AttributeError(f"Config has no attribute {name}")


def get_default_config() -> str:
    return """\
model = "deepseek/deepseek-v4-flash"
max_iterations = 100
base_url = "https://openrouter.ai/api/v1"

[telegram]
BOT_TOKEN = ""
ALLOW_FROM = []  # List of allowed Telegram user IDs (integers). Must be non-empty.

[discord]
TOKEN = ""
ALLOW_FROM = []  # List of allowed Discord user IDs (integers). Empty means allow all.

[websocket]
HOST = "127.0.0.1"
PORT = 8765
"""
