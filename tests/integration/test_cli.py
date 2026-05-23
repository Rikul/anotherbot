"""Integration tests for the CLI entry-point.

These tests exercise the full pipeline from ``main()`` all the way through
``Agent`` and ``agent_loop``.  Only the external OpenAI HTTP client is
mocked so that every internal layer (argument parsing, 
agent construction, iteration logic, tool dispatch) runs as it would in
production.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import app.config as config_module
from app.main import main
from app.infra.app_logging import log as app_log


def _make_llm_response(content: str) -> MagicMock:
    """Return a minimal mock that looks like an OpenAI chat-completion response."""
    mock_message = MagicMock()
    mock_message.tool_calls = None
    mock_message.content = content

    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = "stop"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    return mock_response


@pytest.mark.asyncio
async def test_cli_with_simple_prompt(capsys):
    """Running the CLI with a simple prompt produces the expected response.

    The test uses ``--silent`` so that the final answer is written to stdout
    via ``print()``, making it straightforward to capture and assert on.
    It mocks only the OpenAI client; all other layers (argparse, Agent,
    agent_loop, config, …) run for real.
    """
    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(return_value=_make_llm_response("Hello, world!"))

    # --silent sets the module-level log level; restore it after the test so
    # other tests that check the default level are not affected.
    original_log_level = app_log.level
    try:
        with patch("sys.argv", ["prog", "cli", "-p", "say hello", "--silent"]), \
             patch("app.core.agent.Client") as MockClient, \
             patch("app.cli.cli_agent.get_default_sys_prompt", return_value=""), \
             patch("app.cli.cli_agent.MessageHistory") as MockHistory, \
             patch("app.main.config.load"), \
             patch.object(config_module, "_config", {"model": "test-model"}):

            MockClient.return_value.get_client.return_value = mock_openai
            MockHistory.return_value.get_history.return_value = []
            await main()
    finally:
        app_log.setLevel(original_log_level)

    captured = capsys.readouterr()

    # The LLM reply must appear in stdout
    assert "Hello, world!" in captured.out

    # The OpenAI client was called exactly once (no tool calls → single iteration)
    mock_openai.chat.completions.create.assert_called_once()

    # The call used the model name from config
    _, call_kwargs = mock_openai.chat.completions.create.call_args
    assert call_kwargs["model"] == "test-model"

    # The user prompt was included in the messages sent to the model
    messages = call_kwargs["messages"]
    assert any(
        isinstance(m, dict) and m.get("role") == "user" and "say hello" in m.get("content", "")
        for m in messages
    )