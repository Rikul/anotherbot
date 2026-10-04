import pytest
from unittest.mock import patch, MagicMock

import app.config as config
from app.core.client import Client

# Env vars are cleared before every test by the autouse fixture in conftest.py.


def test_client_raises_when_no_api_key():
    config.load()
    with pytest.raises(RuntimeError, match="API_KEY is not set"):
        Client(api_key=None)


def test_client_uses_api_key_and_base_url_from_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-env")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example.com/v1")
    config.load()
    with patch("app.core.client.AsyncOpenAI") as MockOpenAI:
        Client()
    MockOpenAI.assert_called_once_with(api_key="sk-env", base_url="https://env.example.com/v1")


def test_client_uses_default_base_url_when_env_not_set(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-env")
    config.load()
    with patch("app.core.client.AsyncOpenAI") as MockOpenAI:
        Client()
    MockOpenAI.assert_called_once_with(api_key="sk-env", base_url="https://openrouter.ai/api/v1")


def test_client_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-env")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example.com/v1")
    config.load()
    with patch("app.core.client.AsyncOpenAI") as MockOpenAI:
        Client(api_key="test-key", base_url="https://custom.api.com")
    MockOpenAI.assert_called_once_with(api_key="test-key", base_url="https://custom.api.com")


def test_client_get_client_returns_openai_instance():
    with patch("app.core.client.AsyncOpenAI") as MockOpenAI:
        mock_instance = MagicMock()
        MockOpenAI.return_value = mock_instance
        client = Client(api_key="test-key", base_url="https://example.com")
        assert client.get_client() is mock_instance
