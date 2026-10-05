"""App configuration, built from environment variables (and ``.env`` files).

``load()`` must be called once at startup; afterwards use ``get()`` or
attribute access (``config.telegram``). ``PROJECT_HOME`` and ``APP_DB`` are
fixed at import time.
"""

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


# Plain string env vars -> config path (section, key) or (key,).
_STRING_ENV_VARS: dict[str, tuple[str, ...]] = {
    "TELEGRAM_BOT_TOKEN": ("telegram", "BOT_TOKEN"),
    "DISCORD_BOT_TOKEN": ("discord", "TOKEN"),
    "WEBSOCKET_HOST": ("websocket", "HOST"),
    "WEB_PASSWORD": ("web_password",),
    "LLM_BASE_URL": ("base_url",),
    "LLM_MODEL": ("model",),
    "LLM_API_KEY": ("api_key",),
}
# Comma-separated user ID lists -> config path.
_ID_LIST_ENV_VARS: dict[str, tuple[str, ...]] = {
    "TELEGRAM_ALLOW_FROM": ("telegram", "ALLOW_FROM"),
    "DISCORD_ALLOW_FROM": ("discord", "ALLOW_FROM"),
}
# Integer env vars -> config path. Invalid values raise ValueError.
_INT_ENV_VARS: dict[str, tuple[str, ...]] = {
    "WEBSOCKET_PORT": ("websocket", "PORT"),
    "MAX_ITERATIONS": ("max_iterations",),
}


def _put(cfg: dict, path: tuple[str, ...], value) -> None:
    """Set ``value`` at ``path`` (a top-level key, or a section and key)."""
    if len(path) == 1:
        cfg[path[0]] = value
    else:
        cfg.setdefault(path[0], {})[path[1]] = value


def _parse_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got: {value!r}") from None


def load() -> None:
    """(Re)build the config from defaults plus environment variables.

    Empty variables are ignored. A sub-section such as ``websocket`` only
    exists when one of its variables is set, which is what enables that
    channel.

    Raises:
        ValueError: if an integer variable (``WEBSOCKET_PORT``,
            ``MAX_ITERATIONS``) or an ID list is not a valid integer.
    """
    cfg: dict = {
        "model": "deepseek/deepseek-v4.1-flash",
        "base_url": "https://openrouter.ai/api/v1",
        "max_iterations": 250,
        "telegram": {},
    }
    for name, path in _STRING_ENV_VARS.items():
        if value := os.environ.get(name):
            _put(cfg, path, value)
    for name, path in _ID_LIST_ENV_VARS.items():
        if value := os.environ.get(name):
            _put(cfg, path, [int(x.strip()) for x in value.split(",") if x.strip()])
    for name, path in _INT_ENV_VARS.items():
        if value := os.environ.get(name):
            _put(cfg, path, _parse_int(name, value))

    # Mutate in place so modules holding a reference to _config see the update.
    _config.clear()
    _config.update(cfg)


def get(key: str, default=None):
    """Return the config value for ``key``, or ``default`` if it isn't set."""
    return _config.get(key, default)


def set(key: str, value) -> None:  # pylint: disable=redefined-builtin  # public API: config.set()
    """Set a config value at runtime."""
    _config[key] = value


def __getattr__(name: str):
    if name in _config:
        return _config[name]
    raise AttributeError(f"Config has no attribute {name}")
