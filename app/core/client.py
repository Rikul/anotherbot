from __future__ import annotations

import os
from .. import config


_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

_GEMINI_PROVIDERS = {"gemini", "google"}


class _Completions:
    def __init__(self, api_key: str, api_base: str | None) -> None:
        self._api_key = api_key
        self._api_base = api_base or None

    async def create(self, **kwargs):
        import litellm  # lazy — avoids import-time pydantic/Python version issues

        api_key = self._api_key
        api_base = self._api_base

        # If the model has a provider prefix and a provider-specific key exists
        # in the environment, let litellm route natively instead of forcing the
        # global key/base (which would send traffic through OpenRouter).
        model = kwargs.get("model", "")
        if "/" in model:
            provider = model.split("/")[0].lower()
            env_key = "GOOGLE_API_KEY" if provider in _GEMINI_PROVIDERS else f"{provider.upper()}_API_KEY"
            if os.environ.get(env_key) and api_base in (None, _OPENROUTER_BASE):
                api_key = None
                api_base = None

        params = {}
        if api_key is not None:
            params["api_key"] = api_key
        if api_base is not None:
            params["api_base"] = api_base

        return await litellm.acompletion(**params, **kwargs)


class _Chat:
    def __init__(self, api_key: str, api_base: str | None) -> None:
        self.completions = _Completions(api_key, api_base)


class LiteLLMClient:
    def __init__(self, api_key: str, api_base: str | None) -> None:
        self.chat = _Chat(api_key, api_base)


class Client:

    def __init__(self, api_key: str = None, base_url: str = None) -> None:
        if api_key is None:
            api_key = os.environ.get("LLM_API_KEY") or config.get("api_key", None)

        if base_url is None:
            base_url = os.environ.get("LLM_BASE_URL") or config.get("base_url", "https://openrouter.ai/api/v1")

        if not api_key:
            raise RuntimeError("LLM_API_KEY is not set")

        self._client = LiteLLMClient(api_key=api_key, api_base=base_url)

    def get_client(self):
        return self._client
