"""Integration tests for the CLI entry-point.

These tests exercise the full pipeline from ``main()`` all the way through
``Agent`` and ``agent_loop``.  Only the external OpenAI HTTP client is
mocked so that every internal layer (argument parsing, 
agent construction, iteration logic, tool dispatch) runs as it would in
production.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.main import main


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
async def test_cli_with_simple_prompt(capsys, monkeypatch):
    """Running the CLI with a simple prompt produces the expected response.

    The test uses ``--quiet`` so log lines stay off the console; the final
    answer is still written to stdout via ``print()`` for easy assertion.
    It mocks only the OpenAI client; all other layers (argparse, Agent,
    agent_loop, config loaded from env vars, …) run for real.
    """
    mock_openai = MagicMock()
    mock_openai.chat.completions.create = AsyncMock(return_value=_make_llm_response("Hello, world!"))

    # Config comes from env vars only; main() runs the real config.load().
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    with patch("sys.argv", ["prog", "cli", "-p", "say hello", "--quiet"]), \
         patch("app.core.agent.Client") as MockClient, \
         patch("app.cli.cli_agent.get_default_sys_prompt", return_value=""), \
         patch("app.cli.cli_agent.MessageHistory") as MockHistory:

        MockClient.return_value.get_client.return_value = mock_openai
        MockHistory.return_value.get_history.return_value = []
        await main()

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