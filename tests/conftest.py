import pytest

import app.config as config

# Every env var app.config.load() reads. Cleared before each test so values from
# the developer's shell or a .env file (load_dotenv runs when app.main is
# imported) can't leak into tests; tests opt in with monkeypatch.setenv().
CONFIG_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "MAX_ITERATIONS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOW_FROM",
    "DISCORD_BOT_TOKEN",
    "DISCORD_ALLOW_FROM",
    "WEBSOCKET_HOST",
    "WEBSOCKET_PORT",
)


@pytest.fixture(autouse=True)
def isolated_config_env(monkeypatch):
    """Start every test with no config env vars and restore app.config afterwards."""
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "_config", dict(config._config))
    yield
