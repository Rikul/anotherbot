import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.core.helper_agent import HelperAgent
from app.infra.tracer import Tracer


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _store(tmp_path, **extra):
    return {"trace": True, "tracedir": tmp_path, "model": "test-model", **extra}


def test_start_turn_writes_session_history_and_turn_start(tmp_path):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "hello"},
    ]
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", _store(tmp_path)):
        tracer.start_turn(messages, conversation_id=7)
        path = tracer.path
        tracer.close()
    assert path.name.startswith("trace_cli_c7_")
    assert path.suffix == ".jsonl"
    session, history, turn = _events(path)
    assert session["event"] == "session"
    assert session["model"] == "test-model"
    assert session["conversation_id"] == 7
    assert history["messages"] == messages[1:3]
    assert turn["event"] == "turn_start"
    assert turn["system"] == "sys"
    assert turn["message"] == {"role": "user", "content": "hello"}


def test_file_is_reused_across_turns_of_same_conversation(tmp_path):
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", _store(tmp_path)):
        tracer.start_turn([{"role": "user", "content": "a"}], conversation_id=1)
        first = tracer.path
        tracer.record("turn_end", final="x")
        tracer.start_turn([{"role": "user", "content": "b"}], conversation_id=1)
        assert tracer.path == first
        tracer.close()
    events = [e["event"] for e in _events(first)]
    assert events == ["session", "history", "turn_start", "turn_end", "turn_start"]


def test_conversation_change_rotates_file(tmp_path):
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", _store(tmp_path)):
        tracer.start_turn([{"role": "user", "content": "a"}], conversation_id=1)
        first = tracer.path
        tracer.start_turn([{"role": "user", "content": "b"}], conversation_id=2)
        second = tracer.path
        tracer.close()
    assert first != second
    assert len(list(tmp_path.glob("*.jsonl"))) == 2


def test_disabled_writes_nothing(tmp_path):
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", _store(tmp_path, trace=False)):
        tracer.start_turn([{"role": "user", "content": "a"}])
        tracer.record("turn_end", final="x")
    assert not list(tmp_path.iterdir())


def test_creates_missing_directory(tmp_path):
    tracedir = tmp_path / "nested" / "traces"
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", _store(tracedir)):
        tracer.start_turn([{"role": "user", "content": "a"}])
        tracer.close()
    assert len(list(tracedir.glob("*.jsonl"))) == 1


def test_open_failure_is_swallowed(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file")
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", _store(blocker / "subdir")):
        tracer.start_turn([{"role": "user", "content": "a"}])
        tracer.record("turn_end", final="x")
    assert tracer.path is None


def test_attachment_data_urls_are_redacted(tmp_path):
    data_url = "data:image/png;base64," + "A" * 1000
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", _store(tmp_path)):
        tracer.start_turn([msg])
        path = tracer.path
        tracer.close()
    turn = _events(path)[-1]
    assert turn["message"]["content"][1]["image_url"]["url"] == (
        "data:image/png;base64,<1000 chars omitted>"
    )


@pytest.mark.asyncio
async def test_loop_records_full_trajectory_with_tool_calls(tmp_path):
    tc = MagicMock()
    tc.id = "tc1"
    tc.function.name = "bash"
    tc.function.arguments = '{"command": "echo hi"}'
    tc.model_dump.return_value = {"id": "tc1", "function": {"name": "bash"}}

    def response(tool_calls, content):
        msg = MagicMock()
        msg.role = "assistant"
        msg.tool_calls = tool_calls
        msg.content = content
        msg.model_dump.return_value = {"reasoning": "thinking..."}
        choice = MagicMock(message=msg, finish_reason="tool_calls" if tool_calls else "stop")
        chat = MagicMock(choices=[choice])
        chat.usage.model_dump.return_value = {"total_tokens": 42}
        return chat

    with patch("app.core.agent.Client"):
        agent = HelperAgent()
    agent.client = MagicMock()
    agent.client.chat.completions.create = AsyncMock(
        side_effect=[response([tc], None), response(None, "done")]
    )
    with patch("app.core.runtime._store", _store(tmp_path)), \
         patch("app.core.tool_calls.run_tool", return_value="hi\n"):
        result = await agent.run("say hi")
    assert result == "done"

    (path,) = tmp_path.glob("trace_helper_*.jsonl")
    events = _events(path)
    assert [e["event"] for e in events] == [
        "session", "history", "turn_start",
        "llm_response", "tool_result", "llm_response", "turn_end",
    ]
    first = events[3]
    assert first["message"]["tool_calls"] == [{"id": "tc1", "function": {"name": "bash"}}]
    assert first["message"]["reasoning_content"] == "thinking..."
    assert first["usage"] == {"total_tokens": 42}
    assert "duration_s" in first
    assert events[4]["name"] == "bash"
    assert events[4]["content"] == "hi\n"
    assert events[-1]["final"] == "done"


@pytest.mark.asyncio
async def test_loop_records_error(tmp_path):
    with patch("app.core.agent.Client"):
        agent = HelperAgent()
    agent.client = MagicMock()
    agent.client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.core.runtime._store", _store(tmp_path)):
        with pytest.raises(RuntimeError):
            await agent.run("hi")
    (path,) = tmp_path.glob("trace_helper_*.jsonl")
    last = _events(path)[-1]
    assert last["event"] == "error"
    assert "boom" in last["error"]
