import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import app.config as config
from app.core.background_agent import BackgroundAgent
from app.channels.channel import ChannelType
from app.channels.message import IncomingMessage
from app.channels.message_queue import MessageQueue

# Mock Channel instance (Channel is now an ABC, not an enum)
_mock_channel = MagicMock()
_mock_channel.channel_type = ChannelType.TELEGRAM
_mock_channel.has_stopped = False


def make_mock_client(tool_calls=None, content="Hello!", finish_reason="stop"):
    mock_message = MagicMock()
    mock_message.tool_calls = tool_calls
    mock_message.content = content

    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = finish_reason

    mock_chat = MagicMock()
    mock_chat.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_chat)
    return mock_client


def make_agent(max_iterations=10):
    mq = MessageQueue()
    with patch("app.core.agent.Client") as MockClient:
        mock_openai = make_mock_client()
        MockClient.return_value.get_client.return_value = mock_openai
        with patch("app.core.background_agent.load_system_context", return_value="You are a helpful assistant."):
            with patch("app.core.background_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
                agent = BackgroundAgent(mq=mq, channel=_mock_channel, max_iterations=max_iterations)
    agent.client = mock_openai
    return agent, mock_openai, mq


@pytest.fixture(autouse=True)
def patch_config():
    with patch.object(config, "_config", {"agent": {"model": "test-model"}}):
        yield


def test_agent_initializes_with_empty_messages():
    agent, _, _ = make_agent()
    assert len(agent.messages) == 0


@pytest.mark.asyncio
async def test_agent_loop_sends_system_context_to_llm():
    agent, mock_client, _ = make_agent()
    with patch("app.core.background_agent.load_system_context", return_value="system prompt"):
        await agent.agent_loop("hello")
    call_messages = mock_client.chat.completions.create.call_args[1]["messages"]
    assert call_messages[0]["role"] == "system"
    assert call_messages[0]["content"] == "system prompt"


def test_agent_stores_channel_and_mq():
    mq = MessageQueue()
    with patch("app.core.agent.Client") as MockClient:
        MockClient.return_value.get_client.return_value = MagicMock()
        with patch("app.core.background_agent.load_system_context", return_value=""):
            with patch("app.core.background_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
                agent = BackgroundAgent(mq=mq, channel=_mock_channel)
    assert agent.channel == _mock_channel
    assert agent.mq is mq


@pytest.mark.asyncio
async def test_agent_loop_adds_user_message():
    agent, _, _ = make_agent()
    await agent.agent_loop("What is 2+2?")
    assert any(m.get("role") == "user" and m.get("content") == "What is 2+2?" for m in agent.messages)


@pytest.mark.asyncio
async def test_agent_loop_appends_assistant_message():
    agent, _, _ = make_agent()
    await agent.agent_loop("Hello")
    agent.history.add_message.assert_called_with("assistant", "Hello!")
    assert agent.messages[-1]["content"] == "Hello!"


@pytest.mark.asyncio
async def test_agent_loop_raises_on_empty_choices():
    agent, mock_client, _ = make_agent()
    mock_response = MagicMock()
    mock_response.choices = []
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("app.core.background_agent.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RuntimeError, match="No choices in API response after 5 retries"):
            await agent.agent_loop("Hello")
    assert mock_sleep.call_count == 5


@pytest.mark.asyncio
async def test_agent_loop_sends_outgoing_message():
    agent, _, mq = make_agent()
    await agent.agent_loop("Hello", metadata={"chat_id": 123})

    assert not mq.outgoing.empty()
    msg = await mq.outgoing.get()
    assert msg.content == "Hello!"
    assert msg.channel == _mock_channel
    assert msg.metadata == {"chat_id": 123}


@pytest.mark.asyncio
async def test_agent_loop_respects_max_iterations():
    agent, mock_client, _ = make_agent(max_iterations=3)

    mock_tool_call = MagicMock()
    mock_tool_call.function.name = "bash"
    mock_tool_call.function.arguments = '{"command": "echo hi"}'
    mock_tool_call.id = "tc1"

    mock_message = MagicMock()
    mock_message.tool_calls = [mock_tool_call]
    mock_message.content = None

    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = "tool_calls"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("app.core.tool_calls.run_tool", return_value="hi\n"):
        await agent.agent_loop("run forever")

    assert mock_client.chat.completions.create.call_count == 3


@pytest.mark.asyncio
async def test_agent_loop_runs_tool_and_sends_final_reply():
    agent, mock_client, mq = make_agent()

    mock_tool_call = MagicMock()
    mock_tool_call.function.name = "bash"
    mock_tool_call.function.arguments = '{"command": "echo hi"}'
    mock_tool_call.id = "tc1"

    msg_with_tool = MagicMock()
    msg_with_tool.tool_calls = [mock_tool_call]
    msg_with_tool.content = None
    choice_with_tool = MagicMock()
    choice_with_tool.message = msg_with_tool
    choice_with_tool.finish_reason = "tool_calls"
    response_with_tool = MagicMock()
    response_with_tool.choices = [choice_with_tool]

    msg_plain = MagicMock()
    msg_plain.tool_calls = None
    msg_plain.content = "done"
    choice_plain = MagicMock()
    choice_plain.message = msg_plain
    choice_plain.finish_reason = "stop"
    response_plain = MagicMock()
    response_plain.choices = [choice_plain]

    mock_client.chat.completions.create = AsyncMock(side_effect=[response_with_tool, response_plain])

    with patch("app.core.tool_calls.run_tool", return_value="hi\n") as mock_run_tool:
        await agent.agent_loop("say hi", metadata={"chat_id": 42})

    mock_run_tool.assert_called_once_with(tool_name="bash", tool_args={"command": "echo hi"})

    msg = await mq.outgoing.get()
    assert "running bash" in msg.content
    assert msg.metadata == {"chat_id": 42}


@pytest.mark.asyncio
async def test_agent_loop_sends_inline_content_with_tool_calls():
    """When the assistant sends content alongside tool calls, it should be forwarded."""
    agent, mock_client, mq = make_agent()

    mock_tool_call = MagicMock()
    mock_tool_call.function.name = "bash"
    mock_tool_call.function.arguments = '{"command": "echo hi"}'
    mock_tool_call.id = "tc1"

    msg_with_tool = MagicMock()
    msg_with_tool.tool_calls = [mock_tool_call]
    msg_with_tool.content = "Running the command now..."
    choice_with_tool = MagicMock()
    choice_with_tool.message = msg_with_tool
    choice_with_tool.finish_reason = "tool_calls"
    response_with_tool = MagicMock()
    response_with_tool.choices = [choice_with_tool]

    msg_plain = MagicMock()
    msg_plain.tool_calls = None
    msg_plain.content = "done"
    choice_plain = MagicMock()
    choice_plain.message = msg_plain
    choice_plain.finish_reason = "stop"
    response_plain = MagicMock()
    response_plain.choices = [choice_plain]

    mock_client.chat.completions.create = AsyncMock(side_effect=[response_with_tool, response_plain])

    with patch("app.core.tool_calls.run_tool", return_value="hi\n"):
        await agent.agent_loop("run something")

    messages = []
    while not mq.outgoing.empty():
        messages.append(await mq.outgoing.get())

    contents = [m.content for m in messages]
    assert "Running the command now..." in ''.join(contents)
    assert "done" in contents


@pytest.mark.asyncio
async def test_agent_loop_clears_stopped_after_completion():
    """channel.clear_stopped() must be called after agent_loop finishes."""
    mq = MessageQueue()
    local_channel = MagicMock()
    local_channel.channel_type = ChannelType.TELEGRAM
    local_channel.has_stopped = False

    with patch("app.core.agent.Client") as MockClient:
        MockClient.return_value.get_client.return_value = make_mock_client()
        with patch("app.core.background_agent.load_system_context", return_value=""):
            with patch("app.core.background_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
                agent = BackgroundAgent(mq=mq, channel=local_channel, max_iterations=10)
    agent.client = make_mock_client()

    await agent.agent_loop("Hello")

    local_channel.clear_stopped.assert_called_once()


@pytest.mark.asyncio
async def test_process_incoming_dispatches_to_agent_loop():
    agent, mock_client, mq = make_agent()

    incoming = IncomingMessage(content="hello from telegram", channel=ChannelType.TELEGRAM, metadata={"chat_id": 99})
    await mq.incoming.put(incoming)

    # Run process_incoming for one iteration then cancel
    import asyncio
    task = asyncio.create_task(agent.process_incoming())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert any(m.get("role") == "user" and m.get("content") == "hello from telegram" for m in agent.messages)


@pytest.mark.asyncio
async def test_agent_loop_gathers_multiple_tool_calls_in_parallel():
    agent, mock_client, mq = make_agent()

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
