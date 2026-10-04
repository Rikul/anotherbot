import pytest

import app.config as config

# Env vars are cleared before every test by the autouse fixture in conftest.py.


# --- defaults ---

def test_load_defaults_without_env_vars():
    config.load()
    assert config.get("model") == "deepseek/deepseek-v4.1-flash"
    assert config.get("base_url") == "https://openrouter.ai/api/v1"
    assert config.get("max_iterations") == 250
    assert config.get("telegram") == {}
    assert config.get("api_key") is None
    assert config.get("discord") is None
    assert config.get("websocket") is None


def test_load_resets_values_from_previous_load(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    config.load()
    monkeypatch.delenv("LLM_MODEL")
    config.load()
    assert config.get("model") == "deepseek/deepseek-v4.1-flash"


def test_empty_env_var_is_ignored(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_BASE_URL", "")
    config.load()
    assert config.get("model") == "deepseek/deepseek-v4.1-flash"
    assert config.get("base_url") == "https://openrouter.ai/api/v1"


# --- LLM settings ---

def test_llm_model_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    config.load()
    assert config.get("model") == "gpt-4o"


def test_legacy_model_env_is_ignored(monkeypatch):
    monkeypatch.setenv("MODEL", "gpt-4o")
    config.load()
    assert config.get("model") == "deepseek/deepseek-v4.1-flash"


def test_llm_base_url_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    config.load()
    assert config.get("base_url") == "https://api.example.com/v1"


def test_llm_api_key_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    config.load()
    assert config.get("api_key") == "sk-test"


def test_max_iterations_env_parsed_to_int(monkeypatch):
    monkeypatch.setenv("MAX_ITERATIONS", "42")
    config.load()
    assert config.get("max_iterations") == 42


def test_max_iterations_env_invalid_raises(monkeypatch):
    monkeypatch.setenv("MAX_ITERATIONS", "lots")
    with pytest.raises(ValueError, match="MAX_ITERATIONS must be an integer"):
        config.load()


# --- Telegram ---

def test_telegram_bot_token_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot123:secret")
    config.load()
    assert config.get("telegram")["BOT_TOKEN"] == "bot123:secret"


def test_telegram_allow_from_env_parsed_to_int_list(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOW_FROM", "111, 222,333,")
    config.load()
    assert config.get("telegram")["ALLOW_FROM"] == [111, 222, 333]


def test_telegram_allow_from_env_single_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOW_FROM", "42")
    config.load()
    assert config.get("telegram")["ALLOW_FROM"] == [42]


# --- Discord ---

def test_discord_env(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")
    monkeypatch.setenv("DISCORD_ALLOW_FROM", "7,8")
    config.load()
    assert config.get("discord") == {"TOKEN": "discord-token", "ALLOW_FROM": [7, 8]}


# --- WebSocket ---

def test_websocket_env(monkeypatch):
    monkeypatch.setenv("WEBSOCKET_HOST", "0.0.0.0")
    monkeypatch.setenv("WEBSOCKET_PORT", "9000")
    config.load()
    assert config.get("websocket") == {"HOST": "0.0.0.0", "PORT": 9000}


def test_websocket_port_env_invalid_raises(monkeypatch):
    monkeypatch.setenv("WEBSOCKET_PORT", "http")
    with pytest.raises(ValueError, match="WEBSOCKET_PORT must be an integer"):
        config.load()


# --- get / set / attribute access ---

def test_get_returns_default_for_missing_key():
    config.load()
    assert config.get("nonexistent", "fallback") == "fallback"
    assert config.get("nonexistent") is None


def test_set_overrides_value():
    config.load()
    config.set("model", "override")
    assert config.get("model") == "override"


def test_getattr_returns_config_section(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    config.load()
    assert config.telegram == {"BOT_TOKEN": "t"}


def test_getattr_raises_for_missing_key():
    config.load()
    with pytest.raises(AttributeError, match="Config has no attribute"):
        _ = config.nonexistent_key


# --- full env-only startup (Docker) ---

def test_docker_scenario_all_env_vars(monkeypatch):
    env = {
        "LLM_API_KEY": "sk-docker",
        "LLM_MODEL": "deepseek/deepseek-v4.1-flash",
        "TELEGRAM_BOT_TOKEN": "bot:token",
        "TELEGRAM_ALLOW_FROM": "99,100",
        "WEBSOCKET_HOST": "0.0.0.0",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.load()
    assert config.get("api_key") == "sk-docker"
    assert config.get("model") == "deepseek/deepseek-v4.1-flash"
    assert config.get("telegram") == {"BOT_TOKEN": "bot:token", "ALLOW_FROM": [99, 100]}
    assert config.get("websocket") == {"HOST": "0.0.0.0"}


# --- .env loading (runs at import time, so exercised in a fresh interpreter) ---

def test_project_home_dotenv_is_loaded(tmp_path):
    import os
    import subprocess
    import sys

    (tmp_path / ".env").write_text("MAX_ITERATIONS=7\n")
    env = {k: v for k, v in os.environ.items() if k not in ("MAX_ITERATIONS",)}
    env["ANOTHERBOT_HOME"] = str(tmp_path)
    out = subprocess.run(
        [sys.executable, "-c",
         "import app.config as c; c.load(); print(c.PROJECT_HOME, c.get('max_iterations'))"],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out == [str(tmp_path), "7"]


def test_real_env_wins_over_project_home_dotenv(tmp_path):
    import os
    import subprocess
    import sys

    (tmp_path / ".env").write_text("MAX_ITERATIONS=7\n")
    env = dict(os.environ, ANOTHERBOT_HOME=str(tmp_path), MAX_ITERATIONS="9")
    out = subprocess.run(
        [sys.executable, "-c", "import app.config as c; c.load(); print(c.get('max_iterations'))"],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "9"
