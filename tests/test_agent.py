import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import app.config as config
from app.cli.cli_agent import CliAgent as Agent


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


_MOCK_CONV = {"id": 1, "name": "Test Conv", "channel": "cli",
              "parent_id": None, "created_at": "2024-01-01", "updated_at": "2024-01-01"}


def make_agent(auto_approve=True, silent=True, max_iterations=10):
    with patch("app.core.agent.Client") as MockClient:
        mock_openai = make_mock_client()
        MockClient.return_value.get_client.return_value = mock_openai
        with patch("app.cli.cli_agent.get_default_sys_prompt", return_value="You are a helpful assistant."):
            with patch("app.cli.cli_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
                with patch("app.cli.cli_agent.ConversationStore") as MockStore:
                    MockStore.return_value.get_last.return_value = _MOCK_CONV
                    MockStore.return_value.get.return_value = _MOCK_CONV
                    MockStore.return_value.load_messages.return_value = []
                    MockStore.return_value.count_user_messages.return_value = 0
                    agent = Agent(
                        auto_approve=auto_approve, silent=silent, max_iterations=max_iterations
                    )
    # Attach the mock client so callers can reconfigure it after construction
    agent.client = mock_openai
    return agent, mock_openai


@pytest.fixture(autouse=True)
def patch_config():
    with patch.object(config, "_config", {"agent": {"model": "test-model"}}):
        yield


@pytest.fixture(autouse=True)
def patch_store():
    with patch("app.cli.cli_agent.ConversationStore") as MockStore:
        MockStore.return_value.get_last.return_value = _MOCK_CONV
        MockStore.return_value.get.return_value = _MOCK_CONV
        MockStore.return_value.load_messages.return_value = []
        MockStore.return_value.count_user_messages.return_value = 0
        yield MockStore


@pytest.mark.asyncio
async def test_agent_loop_sends_system_context_to_llm():
    agent, mock_client = make_agent()
    with patch("app.cli.cli_agent.get_default_sys_prompt", return_value="system prompt"):
        await agent.agent_loop("hello")
    call_messages = mock_client.chat.completions.create.call_args_list[0][1]["messages"]
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
        with patch("app.cli.cli_agent.get_default_sys_prompt", return_value="You are a helpful assistant."):
            with patch("app.cli.cli_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
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
        with patch("app.cli.cli_agent.get_default_sys_prompt", return_value="You are a helpful assistant."):
            with patch("app.cli.cli_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
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
async def test_agent_loop_breaks_on_length_finish_reason():
    with patch("app.core.agent.Client") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value.get_client.return_value = mock_client
        with patch("app.cli.cli_agent.get_default_sys_prompt", return_value="You are a helpful assistant."):
            with patch("app.cli.cli_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
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

    mock_client.chat.completions.create = AsyncMock(return_value=response_partial)

    result = await agent.agent_loop("continue")

    assert mock_client.chat.completions.create.call_count == 1
    assert result == "partial"


@pytest.mark.asyncio
async def test_agent_loop_calls_write_trace_when_enabled(tmp_path):
    agent, _ = make_agent()
    fake_path = tmp_path / "trace_01012026_120000.json"
    with patch("app.core.runtime._store", {"trace": True, "tracedir": tmp_path, "model": "m"}):
        with patch("app.infra.tracer.write_trace", return_value=fake_path) as mock_write:
            await agent.agent_loop("hello")
    mock_write.assert_called_once()
    args = mock_write.call_args[0]
    assert args[1] == tmp_path  # tracedir


@pytest.mark.asyncio
async def test_agent_loop_does_not_call_write_trace_when_disabled(tmp_path):
    agent, _ = make_agent()
    with patch("app.core.runtime._store", {"trace": False, "tracedir": tmp_path, "model": "m"}):
        with patch("app.infra.tracer.write_trace") as mock_write:
            await agent.agent_loop("hello")
    mock_write.assert_not_called()


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


@pytest.mark.asyncio
async def test_agent_loop_sends_image_file_to_llm(tmp_path):
    agent, mock_client = make_agent()
    image_path = tmp_path / "screenshot.png"
    image_path.write_bytes(b"fake image data")

    await agent.agent_loop("What is in this image?", metadata={"files": [str(image_path)]})

    call_messages = mock_client.chat.completions.create.call_args_list[0][1]["messages"]
    user_message = call_messages[1]
    assert user_message["role"] == "user"
    assert user_message["content"][0] == {"type": "text", "text": "What is in this image?"}
    assert user_message["content"][1]["type"] == "image_url"
    assert user_message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    stored_user_msg = agent.messages[-2]
    assert stored_user_msg["role"] == "user"
    assert stored_user_msg["content"] == f"What is in this image? [Attachment: {image_path}]"


@pytest.mark.asyncio
async def test_agent_loop_accepts_single_metadata_file_path(tmp_path):
    agent, mock_client = make_agent()
    image_path = tmp_path / "screenshot.jpg"
    image_path.write_bytes(b"fake image data")

    await agent.agent_loop("Describe this", metadata={"files": str(image_path)})

    user_message = mock_client.chat.completions.create.call_args_list[0][1]["messages"][1]
    assert user_message["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_agent_loop_sends_multiple_image_files_to_llm(tmp_path):
    agent, mock_client = make_agent()
    png_path = tmp_path / "first.png"
    jpg_path = tmp_path / "second.jpg"
    png_path.write_bytes(b"first fake image data")
    jpg_path.write_bytes(b"second fake image data")

    await agent.agent_loop(
        "Compare these images",
        metadata={"files": [str(png_path), str(jpg_path)]},
    )

    user_message = mock_client.chat.completions.create.call_args_list[0][1]["messages"][1]
    assert user_message["content"][0] == {"type": "text", "text": "Compare these images"}
    assert len(user_message["content"]) == 3
    assert user_message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert user_message["content"][2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_agent_loop_sends_metadata_files_to_llm(tmp_path):
    agent, mock_client = make_agent()
    file_path = tmp_path / "notes.txt"
    file_path.write_text("plain text attachment", encoding="utf-8")

    await agent.agent_loop("Summarize this file", metadata={"files": [str(file_path)]})

    user_message = mock_client.chat.completions.create.call_args_list[0][1]["messages"][1]
    assert user_message["content"][0] == {"type": "text", "text": "Summarize this file"}
    assert user_message["content"][1]["type"] == "file"
    assert user_message["content"][1]["file"]["filename"] == "notes.txt"
    assert user_message["content"][1]["file"]["file_data"].startswith("data:text/plain;base64,")


@pytest.mark.asyncio
async def test_agent_loop_sends_image_files_as_images(tmp_path):
    agent, mock_client = make_agent()
    image_path = tmp_path / "diagram.png"
    file_path = tmp_path / "report.pdf"
    image_path.write_bytes(b"fake image data")
    file_path.write_bytes(b"%PDF-1.4 fake pdf data")

    await agent.agent_loop(
        "Use these attachments",
        metadata={"files": [str(image_path), str(file_path)]},
    )

    user_message = mock_client.chat.completions.create.call_args_list[0][1]["messages"][1]
    assert user_message["content"][1]["type"] == "image_url"
    assert user_message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert user_message["content"][2]["type"] == "file"
    assert user_message["content"][2]["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_as_list_rejects_non_path_entries():
    with pytest.raises(TypeError):
        Agent._as_list([123])
    with pytest.raises(TypeError):
        Agent._as_list(123)
    assert Agent._as_list(["a", "b"]) == ["a", "b"]
    assert Agent._as_list("a") == ["a"]
    assert Agent._as_list(None) == []


def test_build_user_message_rejects_combined_oversized(tmp_path, monkeypatch):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_bytes(b"x" * 60)
    f2.write_bytes(b"x" * 60)
    monkeypatch.setattr(Agent, "_MAX_COMBINED_ATTACHMENT_BYTES", 100)
    with pytest.raises(ValueError, match="combined limit"):
        Agent._build_user_message("hi", {"files": [str(f1), str(f2)]})


def test_build_placeholder_content():
    assert Agent._build_placeholder_content("hello", []) == "hello"
    assert Agent._build_placeholder_content("look", ["/tmp/a.png"]) == "look [Attachment: /tmp/a.png]"
    assert Agent._build_placeholder_content("check", ["/a.pdf", "/b.png"]) == "check [Attachment: /a.pdf] [Attachment: /b.png]"
