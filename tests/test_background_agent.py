import asyncio
import json
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
_mock_channel.on_agent_turn = AsyncMock()
_mock_channel.on_trajectory_saved = AsyncMock()


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


_MOCK_CONV = {
    "id": 1,
    "name": "Test Conv",
    "channel": "telegram",
    "parent_id": None,
    "created_at": "2024-01-01",
    "updated_at": "2024-01-01",
}


def make_agent(max_iterations=10):
    mq = MessageQueue()
    with patch("app.core.agent.Client") as MockClient:
        mock_openai = make_mock_client()
        MockClient.return_value.get_client.return_value = mock_openai
        with patch(
            "app.core.background_agent.get_default_sys_prompt",
            return_value="You are a helpful assistant.",
        ):
            with patch("app.core.background_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
                with patch("app.core.background_agent.ConversationStore") as MockStore:
                    MockStore.return_value.get_last.return_value = _MOCK_CONV
                    MockStore.return_value.get.return_value = _MOCK_CONV
                    MockStore.return_value.load_messages.return_value = []
                    MockStore.return_value.count_user_messages.return_value = 0
                    agent = BackgroundAgent(
                        mq=mq, channel=_mock_channel, max_iterations=max_iterations
                    )
    agent.client = mock_openai
    return agent, mock_openai, mq


@pytest.fixture(autouse=True)
def patch_config():
    with patch.object(config, "_config", {"agent": {"model": "test-model"}}):
        yield


@pytest.fixture(autouse=True)
def patch_store():
    with patch("app.core.background_agent.ConversationStore") as MockStore:
        MockStore.return_value.get_last.return_value = _MOCK_CONV
        MockStore.return_value.get.return_value = _MOCK_CONV
        MockStore.return_value.load_messages.return_value = []
        MockStore.return_value.count_user_messages.return_value = 0
        yield MockStore


def test_agent_initializes_with_empty_messages():
    agent, _, _ = make_agent()
    assert len(agent.messages) == 0


@pytest.mark.asyncio
async def test_agent_loop_sends_system_context_to_llm():
    agent, mock_client, _ = make_agent()
    with patch(
        "app.core.background_agent.get_default_sys_prompt", return_value="system prompt"
    ):
        await agent.agent_loop("hello")
    call_messages = mock_client.chat.completions.create.call_args[1]["messages"]
    assert call_messages[0]["role"] == "system"
    assert call_messages[0]["content"] == "system prompt"


def test_agent_stores_channel_and_mq():
    mq = MessageQueue()
    with patch("app.core.agent.Client") as MockClient:
        MockClient.return_value.get_client.return_value = MagicMock()
        with patch("app.core.background_agent.get_default_sys_prompt", return_value=""):
            with patch("app.core.background_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
                agent = BackgroundAgent(mq=mq, channel=_mock_channel)
    assert agent.channel == _mock_channel
    assert agent.mq is mq


@pytest.mark.asyncio
async def test_agent_loop_adds_user_message():
    agent, _, _ = make_agent()
    await agent.agent_loop("What is 2+2?")
    assert any(
        m.get("role") == "user" and m.get("content") == "What is 2+2?"
        for m in agent.messages
    )


@pytest.mark.asyncio
async def test_agent_loop_appends_assistant_message():
    agent, _, _ = make_agent()
    await agent.agent_loop("Hello")
    agent.history.add_message.assert_called_with(
        "assistant", "Hello!", agent.conversation_id
    )
    assert agent.messages[-1]["content"] == "Hello!"


@pytest.mark.asyncio
async def test_agent_loop_raises_on_empty_choices():
    agent, mock_client, _ = make_agent()
    mock_response = MagicMock()
    mock_response.choices = []
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch(
        "app.core.background_agent.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        with pytest.raises(
            RuntimeError, match="No choices in API response after 5 retries"
        ):
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
    assert msg.metadata == {"chat_id": 123, "final": True}


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

    mock_client.chat.completions.create = AsyncMock(
        side_effect=[response_with_tool, response_plain]
    )

    with patch("app.core.tool_calls.run_tool", return_value="hi\n") as mock_run_tool:
        await agent.agent_loop("say hi", metadata={"chat_id": 42})

    mock_run_tool.assert_called_once_with(
        tool_name="bash", tool_args={"command": "echo hi"}
    )

    msg = await mq.outgoing.get()
    assert "> bash" in msg.content
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

    mock_client.chat.completions.create = AsyncMock(
        side_effect=[response_with_tool, response_plain]
    )

    with patch("app.core.tool_calls.run_tool", return_value="hi\n"):
        await agent.agent_loop("run something")

    messages = []
    while not mq.outgoing.empty():
        messages.append(await mq.outgoing.get())

    contents = [m.content for m in messages]
    assert "Running the command now..." in "".join(contents)
    assert "done" in contents


@pytest.mark.asyncio
async def test_agent_loop_clears_stopped_after_completion():
    """channel.clear_stopped() must be called after agent_loop finishes."""
    mq = MessageQueue()
    local_channel = MagicMock()
    local_channel.channel_type = ChannelType.TELEGRAM
    local_channel.has_stopped = False
    local_channel.on_trajectory_saved = AsyncMock()

    with patch("app.core.agent.Client") as MockClient:
        MockClient.return_value.get_client.return_value = make_mock_client()
        with patch("app.core.background_agent.get_default_sys_prompt", return_value=""):
            with patch("app.core.background_agent.MessageHistory") as MockHistory:
                MockHistory.return_value.get_history.return_value = []
                agent = BackgroundAgent(mq=mq, channel=local_channel, max_iterations=10)
    agent.client = make_mock_client()

    await agent.agent_loop("Hello")

    local_channel.clear_stopped.assert_called_once()


@pytest.mark.asyncio
async def test_process_incoming_dispatches_to_agent_loop():
    agent, mock_client, mq = make_agent()

    incoming = IncomingMessage(
        content="hello from telegram",
        channel=ChannelType.TELEGRAM,
        metadata={"chat_id": 99},
    )
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

    assert any(
        m.get("role") == "user" and m.get("content") == "hello from telegram"
        for m in agent.messages
    )


@pytest.mark.asyncio
async def test_process_incoming_sends_error_to_user_on_agent_loop_failure():
    agent, _, mq = make_agent()

    with patch.object(agent, "agent_loop", side_effect=RuntimeError("model not found")):
        incoming = IncomingMessage(
            content="hi", channel=ChannelType.TELEGRAM, metadata={"chat_id": 99}
        )
        await mq.incoming.put(incoming)

        task = asyncio.create_task(agent.process_incoming())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert not mq.outgoing.empty()
    error_msg = await mq.outgoing.get()
    assert "model not found" in error_msg.content
    # tagged with the turn's conversation, like the turn's replies
    assert error_msg.metadata == {
        "chat_id": 99,
        "conversation_id": agent.conversation_id,
    }


@pytest.mark.asyncio
async def test_process_incoming_tags_turn_metadata_with_the_conversation():
    agent, _, mq = make_agent()
    agent.conversation_id = 42
    with patch.object(agent, "agent_loop", AsyncMock()) as loop:
        await mq.incoming.put(
            IncomingMessage(
                content="hi", channel=ChannelType.TELEGRAM, metadata={"chat_id": 1}
            )
        )
        task = asyncio.create_task(agent.process_incoming())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    loop.assert_awaited_once_with("hi", {"chat_id": 1, "conversation_id": 42})


@pytest.mark.asyncio
async def test_agent_loop_writes_trace_when_enabled(tmp_path):
    agent, _, _ = make_agent()
    mock_store = {"trace": True, "tracedir": tmp_path, "model": "m"}
    with patch("app.core.runtime._store", mock_store):
        await agent.agent_loop("hello")
    files = list(tmp_path.glob("trace_telegram_c1_*.jsonl"))
    assert len(files) == 1
    assert mock_store["last_trace"] == files[0].name
    events = [json.loads(line)["event"] for line in files[0].read_text().splitlines()]
    assert events == ["session", "history", "turn_start", "llm_response", "turn_end"]


@pytest.mark.asyncio
async def test_agent_loop_does_not_write_trace_when_disabled(tmp_path):
    agent, _, _ = make_agent()
    with patch(
        "app.core.runtime._store", {"trace": False, "tracedir": tmp_path, "model": "m"}
    ):
        await agent.agent_loop("hello")
    assert not list(tmp_path.iterdir())


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

    mock_client.chat.completions.create = AsyncMock(
        side_effect=[response_with_tools, response_final]
    )

    with patch("app.core.tool_calls.run_tool", return_value="result"):
        with patch(
            "app.core.agent.asyncio.gather", wraps=asyncio.gather
        ) as mock_gather:
            await agent.agent_loop("run two tools")

    mock_gather.assert_called_once()
    assert len(mock_gather.call_args[0]) == 2


@pytest.mark.asyncio
async def test_process_incoming_reports_turn_start_and_end_and_clears_stale_stop():
    agent, _, mq = make_agent()
    channel = MagicMock()
    channel.channel_type = ChannelType.TELEGRAM
    channel.has_stopped = False
    calls = []
    channel.clear_stopped.side_effect = lambda: calls.append("clear")
    channel.on_agent_turn = AsyncMock(side_effect=lambda running: calls.append(running))
    agent.channel = channel

    async def fake_loop(*_args):
        calls.append("loop")

    with patch.object(agent, "agent_loop", side_effect=fake_loop):
        await mq.incoming.put(
            IncomingMessage(content="hi", channel=ChannelType.TELEGRAM, metadata={})
        )
        await mq.incoming.put(
            IncomingMessage(
                content="/status", channel=ChannelType.TELEGRAM, metadata={}
            )
        )
        task = asyncio.create_task(agent.process_incoming())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # a stop sent while idle is cleared before the turn; commands aren't turns
    assert calls == ["clear", True, "loop", False]


@pytest.mark.asyncio
async def test_process_incoming_reports_turn_end_even_when_agent_loop_fails():
    agent, _, mq = make_agent()
    channel = MagicMock()
    channel.channel_type = ChannelType.TELEGRAM
    channel.on_agent_turn = AsyncMock()
    agent.channel = channel

    with patch.object(agent, "agent_loop", side_effect=RuntimeError("boom")):
        await mq.incoming.put(
            IncomingMessage(content="hi", channel=ChannelType.TELEGRAM, metadata={})
        )
        task = asyncio.create_task(agent.process_incoming())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert [c.args for c in channel.on_agent_turn.await_args_list] == [
        (True,),
        (False,),
    ]


@pytest.mark.asyncio
async def test_registry_stop_between_turns_does_not_set_the_flag():
    agent, _, _ = make_agent()
    channel = MagicMock()
    channel.stopped = False
    agent.channel = channel
    assert await agent.registry.execute("stop") == "Nothing to stop."
    assert channel.stopped is False


@pytest.mark.asyncio
async def test_restart_command_requests_restart():
    from app.infra import restart

    agent, _, _ = make_agent()
    reply = await agent.registry.execute("restart")
    assert reply.startswith("Restarting")
    assert restart.requested()


@pytest.mark.asyncio
async def test_process_incoming_marks_turns_so_restart_waits_for_them():
    from app.infra import restart

    agent, _, mq = make_agent()
    channel = MagicMock()
    channel.channel_type = ChannelType.TELEGRAM
    channel.on_agent_turn = AsyncMock()
    agent.channel = channel
    seen = []

    async def fake_loop(*_args):
        seen.append(restart._state.active_turns)

    with patch.object(agent, "agent_loop", side_effect=fake_loop):
        await mq.incoming.put(
            IncomingMessage(content="hi", channel=ChannelType.TELEGRAM, metadata={})
        )
        task = asyncio.create_task(agent.process_incoming())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert seen == [1]
    assert restart._state.active_turns == 0


@pytest.mark.asyncio
async def test_no_new_turns_while_restart_is_pending():
    from app.infra import restart

    agent, _, mq = make_agent()
    channel = MagicMock()
    channel.channel_type = ChannelType.TELEGRAM
    channel.on_agent_turn = AsyncMock()
    agent.channel = channel
    restart.request_restart()

    with patch.object(agent, "agent_loop", AsyncMock()) as loop:
        await mq.incoming.put(
            IncomingMessage(content="hi", channel=ChannelType.TELEGRAM, metadata={})
        )
        task = asyncio.create_task(agent.process_incoming())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop.assert_not_awaited()
    reply = mq.outgoing.get_nowait()
    assert "Restarting" in reply.content


# --- trajectories ---


@pytest.mark.asyncio
async def test_agent_loop_saves_turn_trajectory_and_notifies_channel(tmp_path):
    """A turn's tool calls, tool results and per-call usage are stored against
    the final assistant message, and the channel hears about it."""
    from openai.types.chat import ChatCompletion
    from app.infra.conversations import ConversationStore
    from app.infra.message_history import MessageHistory

    def completion(message, finish_reason, tokens):
        return ChatCompletion.model_validate(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {"index": 0, "finish_reason": finish_reason, "message": message}
                ],
                "usage": {
                    "prompt_tokens": tokens,
                    "completion_tokens": 1,
                    "total_tokens": tokens + 1,
                },
            }
        )

    tool_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command": "echo hi"}'},
            }
        ],
    }
    final_msg = {"role": "assistant", "content": "Done."}

    agent, mock_client, _ = make_agent()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[
            completion(tool_msg, "tool_calls", 10),
            completion(final_msg, "stop", 20),
        ]
    )
    store = ConversationStore(db_path=tmp_path / "app.db")
    cid = store.create("telegram")
    agent.store = store
    agent.history = MessageHistory("telegram", db_path=tmp_path / "app.db")
    agent.conversation_id = cid
    agent.messages = [{"role": "user", "content": "earlier"}]
    channel = MagicMock()
    channel.channel_type = ChannelType.TELEGRAM
    channel.has_stopped = False
    channel.on_trajectory_saved = AsyncMock()
    agent.channel = channel

    with patch("app.core.tool_calls.run_tool", return_value="hi\n"):
        await agent.agent_loop("run it", {"websocket_id": "c1"})

    msgs = store.load_messages(cid, with_meta=True)
    reply = msgs[-1]
    assert reply["content"] == "Done." and reply["has_trajectory"]
    assert not msgs[0]["has_trajectory"]  # the user message
    traj = store.get_trajectory(reply["id"])
    assert traj["conversation_id"] == cid and traj["channel"] == "telegram"
    assert traj["system_prompt"]
    assert traj["history_message_count"] == 1
    assert [m["role"] for m in traj["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert traj["messages"][0]["content"] == "run it"
    assert traj["messages"][1]["tool_calls"][0]["function"]["name"] == "bash"
    assert traj["messages"][2]["content"] == "hi\n"
    assert [c["finish_reason"] for c in traj["llm_calls"]] == ["tool_calls", "stop"]
    assert traj["usage_total"]["prompt_tokens"] == 30 and traj["iterations"] == 2
    channel.on_trajectory_saved.assert_awaited_once_with(
        reply["id"], cid, {"websocket_id": "c1"}
    )


@pytest.mark.asyncio
async def test_trajectory_save_failure_does_not_break_the_turn():
    agent, _, _ = make_agent()
    agent.store.save_trajectory.side_effect = RuntimeError("disk full")
    channel = MagicMock()
    channel.has_stopped = False
    channel.on_trajectory_saved = AsyncMock()
    agent.channel = channel
    assert await agent.agent_loop("hi") == "Hello!"
    # nothing was stored, so the UI must not be offered a download
    channel.on_trajectory_saved.assert_not_awaited()


# --- trajectories end to end: BackgroundAgent -> WebChannel -> WebSocket frames ---


def _completion(message, finish_reason):
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {"index": 0, "finish_reason": finish_reason, "message": message}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


_TOOL_TURN = {
    "role": "assistant",
    "content": "Let me check",
    "tool_calls": [
        {
            "id": "tc1",
            "type": "function",
            "function": {"name": "bash", "arguments": '{"command": "echo hi"}'},
        }
    ],
}


async def _run_web_turn(tmp_path, responses, stop_after_tools=False, save_fails=False):
    """Run one turn of a web BackgroundAgent and return the frames its tab receives."""
    from app.channels.web_channel import WebChannel
    from app.infra.conversations import ConversationStore
    from app.infra.message_history import MessageHistory

    mq = MessageQueue()
    web = WebChannel(mq=mq)
    ws = AsyncMock()
    web._connections["tab"] = ws
    db = tmp_path / "app.db"
    store = ConversationStore(db_path=db)
    with (
        patch("app.core.agent.Client") as MockClient,
        patch("app.core.background_agent.ConversationStore", return_value=store),
        patch(
            "app.core.background_agent.MessageHistory",
            return_value=MessageHistory("web", db_path=db),
        ),
    ):
        MockClient.return_value.get_client.return_value = MagicMock()
        agent = BackgroundAgent(mq=mq, channel=web)
    agent.client.chat.completions.create = AsyncMock(side_effect=responses)
    if save_fails:
        store.save_trajectory = MagicMock(side_effect=RuntimeError("disk full"))

    async def run_tool(**_kwargs):
        if stop_after_tools:
            web.stopped = True  # /stop arrives while the tool runs
        return "hi"

    with (
        patch("app.core.tool_calls.run_tool", side_effect=run_tool),
        patch.object(agent, "_auto_name", new=AsyncMock()),
    ):
        await agent.agent_loop("check it", {"websocket_id": "tab"})
    while not mq.outgoing.empty():
        await web.send_message(mq.outgoing.get_nowait())
    return [c.args[0] for c in ws.send_json.await_args_list], store


@pytest.mark.asyncio
async def test_web_turn_marks_the_reply_final_and_sends_trajectory_after_it(tmp_path):
    frames, store = await _run_web_turn(
        tmp_path,
        [
            _completion(_TOOL_TURN, "tool_calls"),
            _completion({"role": "assistant", "content": "All good."}, "stop"),
        ],
    )
    assert [(f["type"], f.get("content"), f.get("final")) for f in frames[:-1]] == [
        ("message", "Let me check", None),  # thinking text: no button
        ("message", "> bash [echo hi]...", None),  # status line: no button
        ("message", "All good.", True),  # the reply
    ]
    traj = frames[-1]
    assert traj["type"] == "trajectory"
    assert (
        store.get_trajectory(traj["message_id"])["messages"][-1]["content"]
        == "All good."
    )


@pytest.mark.asyncio
async def test_web_turn_without_a_reply_has_no_final_frame(tmp_path):
    """A stopped turn ends after the tool batch: the UI has no reply row, so it
    drops the trajectory frame instead of putting the button on a status line."""
    frames, _ = await _run_web_turn(
        tmp_path, [_completion(_TOOL_TURN, "tool_calls")], stop_after_tools=True
    )
    assert not any(f.get("final") for f in frames)


@pytest.mark.asyncio
async def test_web_turn_with_failed_save_sends_no_trajectory_frame(tmp_path):
    frames, _ = await _run_web_turn(
        tmp_path,
        [_completion({"role": "assistant", "content": "Hi."}, "stop")],
        save_fails=True,
    )
    assert [f["type"] for f in frames] == ["message"]
    assert frames[0]["final"] is True
