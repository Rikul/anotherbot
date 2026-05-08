import os
import pytest
from unittest.mock import patch
import app.config as config


def test_load_uses_defaults_when_file_not_found(tmp_path):
    config.load(tmp_path / "missing.toml")
    assert config.get("model") == "deepseek/deepseek-v3.2"
    assert config.get("telegram") == {}


def test_load_reads_toml_file(tmp_path):
    toml_file = tmp_path / "test.toml"
    toml_file.write_bytes(b'[agent]\nmodel = "gpt-4o"\n')
    config.load(toml_file)
    assert config.get("agent") == {"model": "gpt-4o"}


def test_get_returns_value_for_existing_key(tmp_path):
    toml_file = tmp_path / "test.toml"
    toml_file.write_bytes(b'[agent]\nmodel = "gpt-4o"\n')
    config.load(toml_file)
    assert config.get("agent") == {"model": "gpt-4o"}


def test_get_returns_default_for_missing_key(tmp_path):
    toml_file = tmp_path / "test.toml"
    toml_file.write_bytes(b'[agent]\nmodel = "gpt-4o"\n')
    config.load(toml_file)
    assert config.get("nonexistent", "fallback") == "fallback"


def test_get_returns_none_by_default_for_missing_key(tmp_path):
    toml_file = tmp_path / "test.toml"
    toml_file.write_bytes(b'[agent]\nmodel = "gpt-4o"\n')
    config.load(toml_file)
    assert config.get("nonexistent") is None


def test_getattr_returns_config_section(tmp_path):
    toml_file = tmp_path / "test.toml"
    toml_file.write_bytes(b'[agent]\nmodel = "gpt-4o"\n')
    config.load(toml_file)
    assert config.agent == {"model": "gpt-4o"}


def test_getattr_raises_for_missing_key(tmp_path):
    toml_file = tmp_path / "test.toml"
    toml_file.write_bytes(b'[agent]\nmodel = "gpt-4o"\n')
    config.load(toml_file)
    with pytest.raises(AttributeError, match="Config has no attribute"):
        _ = config.nonexistent_key


# --- env var overlay ---

def test_model_env_overrides_file(tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_bytes(b'model = "deepseek/deepseek-v3.2"\n')
    with patch.dict(os.environ, {"MODEL": "gpt-4o"}, clear=False):
        config.load(toml_file)
    assert config.get("model") == "gpt-4o"


def test_telegram_bot_token_env_sets_value(tmp_path):
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "bot123:secret"}, clear=False):
        config.load(tmp_path / "missing.toml")
    assert config.get("telegram")["BOT_TOKEN"] == "bot123:secret"


def test_telegram_allow_from_env_parsed_to_int_list(tmp_path):
    with patch.dict(os.environ, {"TELEGRAM_ALLOW_FROM": "111,222,333"}, clear=False):
        config.load(tmp_path / "missing.toml")
    assert config.get("telegram")["ALLOW_FROM"] == [111, 222, 333]


def test_telegram_allow_from_env_single_id(tmp_path):
    with patch.dict(os.environ, {"TELEGRAM_ALLOW_FROM": "42"}, clear=False):
        config.load(tmp_path / "missing.toml")
    assert config.get("telegram")["ALLOW_FROM"] == [42]


def test_env_vars_override_file_values(tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_bytes(b'model = "deepseek/v3"\n[telegram]\nBOT_TOKEN = "file-token"\n')
    with patch.dict(os.environ, {"MODEL": "claude-opus-4", "TELEGRAM_BOT_TOKEN": "env-token"}, clear=False):
        config.load(toml_file)
    assert config.get("model") == "claude-opus-4"
    assert config.get("telegram")["BOT_TOKEN"] == "env-token"


def test_docker_scenario_no_file_all_env_vars(tmp_path):
    """No config file + env vars only — the expected Docker startup path."""
    env = {
        "MODEL": "deepseek/deepseek-v3.2",
        "TELEGRAM_BOT_TOKEN": "bot:token",
        "TELEGRAM_ALLOW_FROM": "99,100",
    }
    with patch.dict(os.environ, env, clear=False):
        config.load(tmp_path / "missing.toml")
    assert config.get("model") == "deepseek/deepseek-v3.2"
    assert config.get("telegram")["BOT_TOKEN"] == "bot:token"
    assert config.get("telegram")["ALLOW_FROM"] == [99, 100]

