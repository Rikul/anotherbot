import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.core.client import Client, LiteLLMClient


def test_client_raises_when_no_api_key():
    env = {k: v for k, v in os.environ.items() if k not in ("LLM_API_KEY",)}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="API_KEY is not set"):
            Client(api_key=None)


def test_client_get_client_returns_litellm_client():
    client = Client(api_key="test-key", base_url="https://example.com")
    result = client.get_client()
    assert isinstance(result, LiteLLMClient)
    assert hasattr(result.chat, "completions")
    assert hasattr(result.chat.completions, "create")


def test_client_uses_provided_base_url():
    client = Client(api_key="test-key", base_url="https://custom.api.com")
    result = client.get_client()
    assert result.chat.completions._api_base == "https://custom.api.com"


def test_client_uses_default_base_url_when_not_set():
    with patch.dict(os.environ, {"LLM_BASE_URL": ""}, clear=False):
        client = Client(api_key="test-key")
        result = client.get_client()
        # Default falls back to OpenRouter
        assert result.chat.completions._api_base == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_litellm_client_create_calls_acompletion():
    client = Client(api_key="test-key", base_url="https://example.com")
    litellm_client = client.get_client()

    mock_litellm = MagicMock()
    mock_litellm.acompletion = AsyncMock(return_value=MagicMock())

    with patch.dict("sys.modules", {"litellm": mock_litellm}):
        await litellm_client.chat.completions.create(model="test-model", messages=[])

    mock_litellm.acompletion.assert_called_once_with(
        api_key="test-key",
        api_base="https://example.com",
        model="test-model",
        messages=[],
    )
