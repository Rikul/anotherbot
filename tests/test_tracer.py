import json
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.core.helper_agent import HelperAgent
import asyncio

from app.infra.tracer import Tracer, close_all, set_tracing


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


@pytest.mark.asyncio
async def test_loop_skips_trace_work_when_disabled(tmp_path):
    with patch("app.core.agent.Client"):
        agent = HelperAgent()
    msg = MagicMock(tool_calls=None, content="done")
    chat = MagicMock(choices=[MagicMock(message=msg, finish_reason="stop")])
    agent.client = MagicMock()
    agent.client.chat.completions.create = AsyncMock(return_value=chat)
    with patch("app.core.runtime._store", _store(tmp_path, trace=False)), \
         patch.object(agent.tracer, "record") as mock_record:
        await agent.run("hi")
    mock_record.assert_not_called()
    chat.usage.model_dump.assert_not_called()


def test_set_tracing_off_closes_open_files_and_on_rotates(tmp_path):
    store = _store(tmp_path)
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", store):
        tracer.start_turn([{"role": "user", "content": "a"}], conversation_id=1)
        first = tracer.path
        set_tracing(False)
        assert tracer.path is None
        assert store["trace"] is False
        set_tracing(True)
        tracer.start_turn([{"role": "user", "content": "b"}], conversation_id=1)
        second = tracer.path
        close_all()
    assert first != second
    assert [e["event"] for e in _events(second)][:2] == ["session", "history"]


def test_record_does_not_open_a_file_without_start_turn(tmp_path):
    tracer = Tracer("cli")
    with patch("app.core.runtime._store", _store(tmp_path)):
        tracer.record("llm_response", iteration=1)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_tool_result_recorded_before_slower_sibling_finishes(tmp_path):
    with patch("app.core.agent.Client"):
        agent = HelperAgent()
    release = asyncio.Event()

    async def fake_handle(tc):
        if tc.id == "slow":
            await release.wait()
        return tc.id

    agent.handle_tool_call = fake_handle
    fast, slow = MagicMock(id="fast"), MagicMock(id="slow")
    fast.function.name = slow.function.name = "bash"
    with patch("app.core.runtime._store", _store(tmp_path)):
        agent.tracer.start_turn([{"role": "user", "content": "go"}])
        task = asyncio.create_task(agent._run_tool_calls([fast, slow], [], 1))
        for _ in range(5):
            await asyncio.sleep(0)
        recorded = [e.get("tool_call_id") for e in _events(agent.tracer.path)]
        assert "fast" in recorded and "slow" not in recorded
        release.set()
        await task
        agent.tracer.close()


# --- event schema ---

_USAGE = {
    "completion_tokens": 2706,
    "prompt_tokens": 26019,
    "total_tokens": 28725,
    "completion_tokens_details": {
        "accepted_prediction_tokens": None,
        "audio_tokens": 0,
        "reasoning_tokens": 1319,
        "rejected_prediction_tokens": None,
        "image_tokens": 0,
    },
    "prompt_tokens_details": {
        "audio_tokens": 0,
        "cached_tokens": 25344,
        "cache_write_tokens": 0,
        "video_tokens": 0,
    },
    "cost": 0.001315242,
    "is_byok": False,
    "cost_details": {
        "upstream_inference_cost": 0.001315242,
        "upstream_inference_prompt_cost": 0.000503442,
        "upstream_inference_completions_cost": 0.0008118,
    },
}


def _completion(message: dict, finish_reason: str):
    """A real openai ChatCompletion, including OpenRouter-style extra fields."""
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(
        {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 1,
            "model": "deepseek/deepseek-v4.1-flash",
            "choices": [
                {"index": 0, "finish_reason": finish_reason, "message": message}
            ],
            "usage": _USAGE,
        }
    )


_TOOL_CALLS = [
    {
        "id": "call_1",
        "type": "function",
        "function": {"name": "bash", "arguments": '{"command": "git status"}'},
    },
    {
        "id": "call_2",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
    },
]


@pytest.mark.asyncio
async def test_trace_event_schema(tmp_path):
    with patch("app.core.agent.Client"):
        agent = HelperAgent()
    agent.client = MagicMock()
    agent.client.chat.completions.create = AsyncMock(
        side_effect=[
            _completion(
                {
                    "role": "assistant",
                    "content": "Checking.",
                    "reasoning": "Need the branch state.",
                    "tool_calls": _TOOL_CALLS,
                },
                "tool_calls",
            ),
            _completion(
                {
                    "role": "assistant",
                    "content": "Review complete.",
                    "reasoning": "Now I have a full picture.",
                },
                "stop",
            ),
        ]
    )

    async def fake_run_tool(tool_name, tool_args):
        return f"{tool_name} output"

    store = _store(tmp_path, model="deepseek/deepseek-v4.1-flash")
    with patch("app.core.runtime._store", store), \
         patch("app.core.tool_calls.run_tool", side_effect=fake_run_tool):
        assert await agent.run("review the branch") == "Review complete."

    (path,) = tmp_path.glob("trace_helper_*.jsonl")
    events = _events(path)  # every line is valid JSON

    # Every event: an ISO timestamp and an event name.
    for e in events:
        assert isinstance(e["event"], str)
        datetime.fromisoformat(e["ts"])
    assert [e["event"] for e in events] == [
        "session", "history", "turn_start",
        "llm_response", "tool_result", "tool_result", "llm_response",
        "turn_end",
    ]

    # llm_response
    responses = [e for e in events if e["event"] == "llm_response"]
    for e in responses:
        assert set(e) == {
            "ts", "event", "iteration", "model", "duration_s",
            "finish_reason", "usage", "message",
        }
        assert e["model"] == "deepseek/deepseek-v4.1-flash"
        assert isinstance(e["duration_s"], float) and e["duration_s"] >= 0
        usage = e["usage"]
        for key in ("completion_tokens", "prompt_tokens", "total_tokens"):
            assert usage[key] == _USAGE[key]
        # Provider-specific usage fields survive serialization.
        assert usage["cost"] == _USAGE["cost"]
        assert usage["is_byok"] is False
        assert usage["cost_details"] == _USAGE["cost_details"]
        assert usage["completion_tokens_details"]["reasoning_tokens"] == 1319
        assert usage["completion_tokens_details"]["image_tokens"] == 0
        assert usage["prompt_tokens_details"]["cached_tokens"] == 25344
        assert usage["prompt_tokens_details"]["cache_write_tokens"] == 0
        assert e["message"]["role"] == "assistant"

    first, second = responses
    assert (first["iteration"], first["finish_reason"]) == (1, "tool_calls")
    assert (second["iteration"], second["finish_reason"]) == (2, "stop")
    assert first["message"]["content"] == "Checking."
    assert first["message"]["reasoning_content"] == "Need the branch state."
    assert [
        (tc["id"], tc["type"], tc["function"]["name"], tc["function"]["arguments"])
        for tc in first["message"]["tool_calls"]
    ] == [
        (tc["id"], tc["type"], tc["function"]["name"], tc["function"]["arguments"])
        for tc in _TOOL_CALLS
    ]
    assert second["message"]["content"] == "Review complete."
    assert second["message"]["reasoning_content"] == "Now I have a full picture."
    assert "tool_calls" not in second["message"]

    # tool_result
    results = sorted(
        (e for e in events if e["event"] == "tool_result"),
        key=lambda e: e["tool_call_id"],
    )
    for e in results:
        assert set(e) == {
            "ts", "event", "iteration", "tool_call_id", "name", "duration_s", "content",
        }
        assert e["iteration"] == 1
        assert isinstance(e["duration_s"], float) and e["duration_s"] >= 0
    assert [(e["tool_call_id"], e["name"], e["content"]) for e in results] == [
        ("call_1", "bash", "bash output"),
        ("call_2", "read_file", "read_file output"),
    ]

    # turn_end
    end = events[-1]
    assert set(end) == {"ts", "event", "iterations", "final"}
    assert (end["iterations"], end["final"]) == (2, "Review complete.")
