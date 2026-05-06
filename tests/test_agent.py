import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import app.config as config
from app.cli.cli_agent import CliAgent as Agent
from app.infra.message_history import MessageHistory


def make_mock_client(tool_calls=None, content="Hello!", finish_reason="stop"):
    """Build a mock AsyncOpenAI client that returns a canned response."""
    mock_message = MagicMock()
    mock_message.tool_calls = tool_calls
    mock_message.content = content
    mock_message.model_dump.return_value = {"role": "assistant", "content": content, "tool_calls": tool_calls or []}

    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = finish_reason

    mock_chat = MagicMock()
    mock_chat.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_chat)
    return mock_client


def make_agent(auto_approve=True, silent=True, max_iterations=10):
    with patch("app.core.agent.Client") as MockClient:
        mock_openai = make_mock_client()
        MockClient.return_value.get_client.return_value = mock_openai
        with patch("app.cli.cli_agent.load_system_context", return_value="You are a helpful assistant."):
            with patch.object(MessageHistory, "get_history", return_value=[]):
                agent = Agent(
                    auto_approve=auto_approve, silent=silent, max_iterations=max_iterations
                )
    # Attach the mock client so callers can reconfigure it after construction
    agent.client = mock_openai
    return agent, mock_openai


@pytest.fixture(autouse=True)
def patch_config():
    with patch.object(config,  "_config", {"agent": {"model": "test-model"}}):
        yield


@pytest.mark.asyncio
async def test_agent_loop_sends_system_context_to_llm():
    agent, mock_client = make_agent()
    with patch("app.cli.cli_agent.load_system_context", return_value="system prompt"):
        await agent.agent_loop("hello")
    call_messages = mock_client.chat.completions.create.call_args[1]["messages"]
    assert call_messages[0]["role"] == "system"
    assert call_messages[0]["content"] == "system prompt"


@pytest.mark.asyncio
async def test_agent_loop_adds_user_message():
    agent, mock_client = make_agent()
    await agent.agent_loop("What is 2+2?")
    assert any(
        m.get("role") == "user" and m.get("content") == "What is 2+2?"
        for m in agent.messages
    )


@pytest.mark.asyncio
async def test_agent_loop_appends_assistant_message():
    agent, mock_client = make_agent()
    await agent.agent_loop("Hello")
    assert agent.messages[-1]["content"] == "Hello!"


@pytest.mark.asyncio
async def test_agent_loop_raises_on_empty_choices():
    agent, mock_client = make_agent()
    mock_response = MagicMock()
    mock_response.choices = []
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with pytest.raises(RuntimeError, match="no choices in response"):
        await agent.agent_loop("Hello")


@pytest.mark.asyncio
async def test_agent_loop_respects_max_iterations():
    with patch("app.core.agent.Client") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value.get_client.return_value = mock_client
        with patch("app.cli.cli_agent.load_system_context", return_value="You are a helpful assistant."):
            with patch.object(MessageHistory, "get_history", return_value=[]):
                agent = Agent(auto_approve=True, silent=True, max_iterations=3)
    agent.client = mock_client

    mock_tool_call = MagicMock()
    mock_tool_call.function.name = "bash"
    mock_tool_call.function.arguments = '{"command": "echo hi"}'
    mock_tool_call.id = "tc1"

    mock_message = MagicMock()
    mock_message.tool_calls = [mock_tool_call]
    mock_message.content = None
    mock_message.model_dump.return_value = {"role": "assistant", "tool_calls": [tool_call.model_dump() for tool_call in mock_tool_call], "content": None}  # Simplified

    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = "tool_calls"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("app.core.tool_calls.run_tool", return_value="hi\n"):
        await agent.agent_loop("run forever")

    # Should have stopped after max_iterations=3 LLM calls
    assert mock_client.chat.completions.create.call_count == 3


@pytest.mark.asyncio
async def test_agent_loop_runs_tool_when_auto_approve():
    with patch("app.core.agent.Client") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value.get_client.return_value = mock_client
        with patch("app.cli.cli_agent.load_system_context", return_value="You are a helpful assistant."):
            with patch.object(MessageHistory, "get_history", return_value=[]):
                agent = Agent(auto_approve=True, silent=True, max_iterations=10)
    agent.client = mock_client

    mock_tool_call = MagicMock()
    mock_tool_call.function.name = "bash"
    mock_tool_call.function.arguments = '{"command": "echo hi"}'
    mock_tool_call.id = "tc1"

    # First response: has a tool call
    msg_with_tool = MagicMock()
    msg_with_tool.tool_calls = [mock_tool_call]
    msg_with_tool.content = None
    msg_with_tool.model_dump.return_value = {"role": "assistant", "tool_calls": [tc.model_dump() for tc in msg_with_tool.tool_calls], "content": None}
    choice_with_tool = MagicMock()
    choice_with_tool.message = msg_with_tool
    choice_with_tool.finish_reason = "tool_calls"
    response_with_tool = MagicMock()
    response_with_tool.choices = [choice_with_tool]

    # Second response: plain text, ends the loop
    msg_plain = MagicMock()
    msg_plain.tool_calls = None
    msg_plain.content = "done"
    msg_plain.model_dump.return_value = {"role": "assistant", "content": "done"}
    choice_plain = MagicMock()
    choice_plain.message = msg_plain
    choice_plain.finish_reason = "stop"
    response_plain = MagicMock()
    response_plain.choices = [choice_plain]

    mock_client.chat.completions.create = AsyncMock(
        side_effect=[response_with_tool, response_plain]
    )

    with patch("app.core.tool_calls.run_tool", return_value="hi\n") as mock_run_tool:
        await agent.agent_loop("say hi")

    mock_run_tool.assert_called_once_with(
        tool_name="bash", tool_args={"command": "echo hi"}
    )


@pytest.mark.asyncio
async def test_agent_loop_continues_when_finish_reason_not_stop():
    with patch("app.core.agent.Client") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value.get_client.return_value = mock_client
        with patch("app.cli.cli_agent.load_system_context", return_value="You are a helpful assistant."):
            with patch.object(MessageHistory, "get_history", return_value=[]):
                agent = Agent(auto_approve=True, silent=True, max_iterations=10)
    agent.client = mock_client

    msg_partial = MagicMock()
    msg_partial.tool_calls = None
    msg_partial.content = "partial"
    msg_partial.model_dump.return_value = {"role": "assistant", "content": "partial"}
    choice_partial = MagicMock()
    choice_partial.message = msg_partial
    choice_partial.finish_reason = "length"
    response_partial = MagicMock()
    response_partial.choices = [choice_partial]

    msg_final = MagicMock()
    msg_final.tool_calls = None
    msg_final.content = "final"
    msg_final.model_dump.return_value = {"role": "assistant", "content": "final"}
    choice_final = MagicMock()
    choice_final.message = msg_final
    choice_final.finish_reason = "stop"
    response_final = MagicMock()
    response_final.choices = [choice_final]

    mock_client.chat.completions.create = AsyncMock(
        side_effect=[response_partial, response_final]
    )

    await agent.agent_loop("continue")

    assert mock_client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_agent_loop_gathers_multiple_tool_calls_in_parallel():
    agent, mock_client = make_agent()

    tc1, tc2 = MagicMock(), MagicMock()
    tc1.function.name = "bash"
    tc1.function.arguments = '{"command": "echo 1"}'
    tc1.id = "tc1"
    tc2.function.name = "bash"
    tc2.function.arguments = '{"command": "echo 2"}'
    tc2.id = "tc2"

    msg_with_tools = MagicMock()
    msg_with_tools.tool_calls = [tc1, tc2]
    msg_with_tools.content = None
    choice_with_tools = MagicMock()
    choice_with_tools.message = msg_with_tools
    choice_with_tools.finish_reason = "tool_calls"
    response_with_tools = MagicMock()
    response_with_tools.choices = [choice_with_tools]

    msg_final = MagicMock()
    msg_final.tool_calls = None
    msg_final.content = "done"
    msg_final.model_dump.return_value = {"role": "assistant", "content": "done"}
    choice_final = MagicMock()
    choice_final.message = msg_final
    choice_final.finish_reason = "stop"
    response_final = MagicMock()
    response_final.choices = [choice_final]

    mock_client.chat.completions.create = AsyncMock(side_effect=[response_with_tools, response_final])

    with patch("app.core.tool_calls.run_tool", return_value="result"):
        with patch("app.core.agent.asyncio.gather", wraps=asyncio.gather) as mock_gather:
            await agent.agent_loop("run two tools")

    mock_gather.assert_called_once()
    assert len(mock_gather.call_args[0]) == 2
