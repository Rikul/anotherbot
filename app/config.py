from __future__ import annotations

import os
import tomllib
from pathlib import Path

_config: dict = {}

APP_NAME = "crafterscode"
PROJECT_HOME = Path(os.environ.get("ANOTHERBOT_HOME", Path.home() / f".{APP_NAME}"))
HOME_CONFIG_PATH = PROJECT_HOME / "config.toml"
APP_DB = PROJECT_HOME / "app.db"


def load(path: Path | str = HOME_CONFIG_PATH) -> None:
    global _config

    if os.path.exists(path):
        with open(path, "rb") as f:
            _config = tomllib.load(f)
    else:
        _config = {
            "model": "deepseek/deepseek-v3.2",
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


def get(key: str, default=None):
    return _config.get(key, default)


def __getattr__(name: str):
    if name in _config:
        return _config[name]
    raise AttributeError(f"Config has no attribute {name}")


def get_default_config() -> str:
    return """\
model = "deepseek/deepseek-v3.2"
max_iterations = 100
base_url = "https://openrouter.ai/api/v1"

[telegram]
BOT_TOKEN = ""
ALLOW_FROM = []  # List of allowed Telegram user IDs (integers). Must be non-empty.
"""
