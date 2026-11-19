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

    with patch("app.core.tool_calls._start_daemon_thread", side_effect=AssertionError):
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


@pytest.mark.asyncio
async def test_hung_cancelled_thread_does_not_block_later_tools_forever():
    import asyncio
    import threading
    from unittest.mock import patch

    from app.core import tool_calls
    from app.core.tool import Tool

    release = threading.Event()

    class HungTool(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "hung"}}

        @staticmethod
        def call() -> str:
            release.wait(5)
            return "late"

    try:
        with (
            patch.dict(tool_calls.tool_registry, {"hung": HungTool}),
            patch.object(tool_calls, "UNFINISHED_WAIT_SECS", 0.2),
        ):
            task = asyncio.ensure_future(tool_calls.run_tool("hung", {}))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            result = await asyncio.wait_for(
                tool_calls.run_tool("todo_list", {}), timeout=2
            )
        assert result.startswith("Error: not run, because hung")
    finally:
        release.set()
        for _ in range(100):
            if not tool_calls.unfinished_tools():
                break
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_parallel_sync_tools_do_not_overlap():
    """Parallel calls from one turn run one at a time, as before threading."""
    import asyncio
    import threading
    import time
    from unittest.mock import patch

    from app.core import tool_calls
    from app.core.tool import Tool

    active = 0
    peak = 0
    lock = threading.Lock()

    class Busy(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "busy"}}

        @staticmethod
        def call() -> str:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return "ok"

    with patch.dict(tool_calls.tool_registry, {"busy": Busy}):
        results = await asyncio.gather(
            *(tool_calls.run_tool("busy", {}) for _ in range(4))
        )
    assert results == ["ok"] * 4
    assert peak == 1


@pytest.mark.asyncio
async def test_call_waiting_on_the_lock_rechecks_unfinished_threads():
    """B waits for the lock while A runs; A is cancelled (thread still running).
    B must not start until A's thread is done."""
    import asyncio
    import threading
    import time
    from unittest.mock import patch

    from app.core import tool_calls
    from app.core.tool import Tool

    order = []
    a_started = threading.Event()

    class A(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "a"}}

        @staticmethod
        def call() -> str:
            a_started.set()
            time.sleep(0.3)
            order.append("a done")
            return "a"

    class B(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "b"}}

        @staticmethod
        def call() -> str:
            order.append("b ran")
            return "b"

    with patch.dict(tool_calls.tool_registry, {"a": A, "b": B}):
        task_a = asyncio.ensure_future(tool_calls.run_tool("a", {}))
        while not a_started.is_set():
            await asyncio.sleep(0.01)
        task_b = asyncio.ensure_future(tool_calls.run_tool("b", {}))
        await asyncio.sleep(0.05)  # B is now waiting for the lock
        task_a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_a
        assert await asyncio.wait_for(task_b, 3) == "b"
    assert order == ["a done", "b ran"]


@pytest.mark.asyncio
async def test_sync_tools_run_on_daemon_threads_with_context():
    """Daemon threads, so quitting never waits for a hung cancelled tool; the
    context (e.g. the todo list) is carried over like asyncio.to_thread does."""
    import contextvars
    import threading
    from unittest.mock import patch

    from app.core import tool_calls
    from app.core.tool import Tool

    var = contextvars.ContextVar("var")
    var.set("from caller")
    seen = {}

    class Probe(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "probe"}}

        @staticmethod
        def call() -> str:
            seen["daemon"] = threading.current_thread().daemon
            seen["var"] = var.get()
            return "ok"

    class Boom(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "boom"}}

        @staticmethod
        def call() -> str:
            raise ValueError("nope")

    with patch.dict(tool_calls.tool_registry, {"probe": Probe, "boom": Boom}):
        assert await tool_calls.run_tool("probe", {}) == "ok"
        assert "nope" in await tool_calls.run_tool("boom", {})
    assert seen == {"daemon": True, "var": "from caller"}


@pytest.mark.asyncio
async def test_parallel_bash_calls_do_not_overlap(tmp_path):
    """bash is async now, but parallel calls from one turn still run one at a
    time, as they did when it blocked the loop."""
    import asyncio

    from app.core.tool_calls import run_tool

    log = tmp_path / "log"
    cmd = f"echo start >> {log}; sleep 0.2; echo end >> {log}"
    await asyncio.gather(*(run_tool("bash", {"command": cmd}) for _ in range(3)))
    assert log.read_text().split() == ["start", "end"] * 3


@pytest.mark.asyncio
async def test_after_the_cap_later_calls_fail_at_once():
    """The 60 s is counted once from the cancel, not again for every call."""
    import asyncio
    import threading
    import time
    from unittest.mock import patch

    from app.core import tool_calls
    from app.core.tool import Tool

    release = threading.Event()

    class HungTool(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "hung2"}}

        @staticmethod
        def call() -> str:
            release.wait(5)
            return "late"

    try:
        with (
            patch.dict(tool_calls.tool_registry, {"hung2": HungTool}),
            patch.object(tool_calls, "UNFINISHED_WAIT_SECS", 0.3),
        ):
            task = asyncio.ensure_future(tool_calls.run_tool("hung2", {}))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            first = await tool_calls.run_tool("todo_list", {})  # waits ~0.3 s
            start = time.monotonic()
            second = await tool_calls.run_tool("todo_list", {})
            third = await tool_calls.run_tool("todo_list", {})
            elapsed = time.monotonic() - start
        assert first.startswith("Error: not run")
        assert second.startswith("Error: not run") and third == second
        assert elapsed < 0.1  # no fresh wait per call
    finally:
        release.set()
        for _ in range(100):
            if not tool_calls.unfinished_tools():
                break
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_a_thread_stuck_forever_is_abandoned_eventually():
    """Past UNFINISHED_ABANDON_SECS a stuck thread stops blocking tool calls."""
    import asyncio
    import threading
    from unittest.mock import patch

    from app.core import tool_calls
    from app.core.tool import Tool

    release = threading.Event()

    class Stuck(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "stuck"}}

        @staticmethod
        def call() -> str:
            release.wait(5)
            return "late"

    try:
        with (
            patch.dict(tool_calls.tool_registry, {"stuck": Stuck}),
            patch.object(tool_calls, "UNFINISHED_WAIT_SECS", 0.1),
            patch.object(tool_calls, "UNFINISHED_ABANDON_SECS", 0.3),
        ):
            task = asyncio.ensure_future(tool_calls.run_tool("stuck", {}))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            refused = await tool_calls.run_tool("todo_list", {})
            assert refused.startswith("Error: not run")
            await asyncio.sleep(0.35)
            ran = await tool_calls.run_tool("todo_list", {})
            assert not ran.startswith("Error")  # runs again, not refused
            assert tool_calls.unfinished_tools() == []
    finally:
        release.set()
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_threaded_tool_raising_stopiteration_returns_an_error():
    """StopIteration can't be stored on a future on Python < 3.14; the call
    must still finish (with an error), not hang holding the tool lock."""
    import asyncio
    from unittest.mock import patch

    from app.core import tool_calls
    from app.core.tool import Tool

    class Stops(Tool):
        @staticmethod
        def spec():
            return {"type": "function", "function": {"name": "stops"}}

        @staticmethod
        def call() -> str:
            raise StopIteration("done early")

    with patch.dict(tool_calls.tool_registry, {"stops": Stops}):
        result = await asyncio.wait_for(tool_calls.run_tool("stops", {}), 2)
        assert result.startswith("Error running tool stops")
        assert "StopIteration" in result
        # and the lock was released: the next threaded call runs
        assert await asyncio.wait_for(tool_calls.run_tool("stops", {}), 2)


@pytest.mark.asyncio
async def test_settle_completes_even_if_the_future_rejects_the_exception():
    """Simulates Python 3.12, where set_exception(StopIteration()) raises."""
    import asyncio
    from unittest.mock import patch

    from app.core import tool_calls

    class PickyFuture(asyncio.futures._PyFuture):  # like 3.12 with StopIteration
        def set_exception(self, exception):
            if isinstance(exception, ValueError):
                raise TypeError("refused")
            super().set_exception(exception)

    def boom():
        raise ValueError("x")

    loop = asyncio.get_running_loop()
    with patch.object(loop, "create_future", lambda: PickyFuture(loop=loop)):
        future = tool_calls._start_daemon_thread("boom", boom, {})
    with pytest.raises(RuntimeError, match="refused"):
        await asyncio.wait_for(future, 2)
