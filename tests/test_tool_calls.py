import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.core.tool_calls import (
    run_tool,
    tool_registry,
    all_tool_specs,
    get_all_tool_specs,
    run_tool_async,
)


def test_tool_registry_contains_expected_tools():
    assert "read_file" in tool_registry
    assert "write_file" in tool_registry
    assert "bash" in tool_registry
    assert "web_fetch" in tool_registry
    assert "get_skills_dir" in tool_registry
    assert "helper_agent" in tool_registry


@pytest.mark.asyncio
async def test_run_tool_calls_correct_function(tmp_path):
    file_path = str(tmp_path / "test.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("content")
    result = await run_tool("read_file", {"file_path": file_path})
    assert result == "content"


@pytest.mark.asyncio
async def test_run_tool_unknown_tool_returns_error():
    result = await run_tool("nonexistent_tool", {})
    assert "Error" in result


# ---------------------------------------------------------------------------
# get_all_tool_specs
# ---------------------------------------------------------------------------


def test_get_all_tool_specs_includes_local_tools():
    mock_mgr = MagicMock()
    mock_mgr.get_tool_specs.return_value = []
    with patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        specs = get_all_tool_specs()
    assert specs == all_tool_specs


def test_get_all_tool_specs_merges_mcp_tools():
    mcp_spec = {
        "type": "function",
        "function": {"name": "srv__tool", "description": "", "parameters": {}},
    }
    mock_mgr = MagicMock()
    mock_mgr.get_tool_specs.return_value = [mcp_spec]
    with patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        specs = get_all_tool_specs()
    assert mcp_spec in specs
    assert len(specs) == len(all_tool_specs) + 1


# ---------------------------------------------------------------------------
# run_tool_async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tool_async_routes_to_mcp():
    mock_mgr = MagicMock()
    mock_mgr.is_mcp_tool.return_value = True
    mock_mgr.call_tool = AsyncMock(return_value="mcp result")
    with patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        result = await run_tool_async("srv__tool", {"key": "val"})
    mock_mgr.call_tool.assert_called_once_with("srv__tool", {"key": "val"})
    assert result == "mcp result"


@pytest.mark.asyncio
async def test_run_tool_async_falls_through_for_local_tool(tmp_path):
    file_path = str(tmp_path / "f.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("hello")
    mock_mgr = MagicMock()
    mock_mgr.is_mcp_tool.return_value = False
    with patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        result = await run_tool_async("read_file", {"file_path": file_path})
    mock_mgr.call_tool.assert_not_called()
    assert result == "hello"


def test_tool_subclass_without_call_is_rejected():
    from app.core.tool import Tool

    with pytest.raises(TypeError, match="must define call"):

        class NoCall(Tool):  # noqa: F841
            @staticmethod
            def spec():
                return {}


def test_every_registered_tool_has_callable_call_and_matching_spec_name():
    from app.core.tool_calls import tool_registry

    for name, cls in tool_registry.items():
        assert callable(cls.call)
        assert cls.spec()["function"]["name"] == name


# --- threads: todo tools stay on the loop, cancellation waits for the thread --


@pytest.mark.asyncio
async def test_parallel_todo_adds_get_unique_ids():
    import asyncio

    from app.core.tool_calls import run_tool
    from app.tools.todo import init_task_todos

    init_task_todos()
    results = await asyncio.gather(
        *(run_tool("todo_add", {"title": f"t{i}"}) for i in range(50))
    )
    ids = [r.split()[1] for r in results]  # "Task <id> added: ..."
    assert len(set(ids)) == 50


@pytest.mark.asyncio
async def test_todo_tools_do_not_run_in_a_thread():
    from unittest.mock import patch

    from app.core.tool_calls import run_tool

    with patch("app.core.tool_calls.asyncio.to_thread", side_effect=AssertionError):
        assert "added" in await run_tool("todo_add", {"title": "x"})


@pytest.mark.asyncio
async def test_cancel_is_immediate_and_next_tool_waits_for_the_thread():
    """Ctrl+C ends the call at once; the next tool call waits for the old thread."""
    import asyncio
    import threading
    import time
    from unittest.mock import patch

    from app.core import tool_calls
    from app.core.tool import Tool

    finished = threading.Event()
    order = []

    class SlowTool(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "slow"}}

        @staticmethod
        def call() -> str:
            time.sleep(0.4)
            order.append("slow done")
            finished.set()
            return "done"

    class NextTool(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "next"}}

        @staticmethod
        def call() -> str:
            order.append("next ran")
            return "ok"

    with patch.dict(tool_calls.tool_registry, {"slow": SlowTool, "next": NextTool}):
        task = asyncio.ensure_future(tool_calls.run_tool("slow", {}))
        await asyncio.sleep(0.05)
        start = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - start < 0.2  # didn't wait for the thread
        assert not finished.is_set()
        assert tool_calls.unfinished_tools() == ["slow"]

        assert await tool_calls.run_tool("next", {}) == "ok"
    assert order == ["slow done", "next ran"]  # no overlap
    assert tool_calls.unfinished_tools() == []
