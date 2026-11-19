"""Built-in tool registry and dispatch (MCP tools are routed via ``mcp_manager``)."""

import asyncio
import contextvars
import inspect
import json
import os
import threading
import weakref

from ..infra.app_logging import log
from ..infra.helpers import trunc_str_with_ellipsis
from ..tools.read_file import ReadFileTool
from ..tools.write_file import WriteFileTool
from ..tools.bash import BashTool
from ..tools.web_fetch import WebFetchTool
from ..tools.get_skills_dir import GetSkillsDirTool
from ..tools.todo import TodoAddTool, TodoListTool, TodoClearTool, TodoUpdateTool
from ..tools.calculator import CalculatorTool
from ..tools.hackernews import HackerNewsTool

from ..tools.web_search import WebSearchText
from ..tools.web_search import WebSearchImages
from ..tools.web_search import WebSearchVideos
from ..tools.web_search import WebSearchNews
from ..tools.web_search import WebSearchBooks

from ..tools.sched_tasks_tool import (
    ListScheduledTasks,
    AddScheduledTask,
    UpdateScheduledTask,
    RemoveScheduledTask,
    GetScheduledTaskOutput,
)

from ..tools.get_city_state import GetCityState
from ..tools.get_datetime import GetDateTime
from ..tools.helper_agent import HelperAgentTool
from ..tools.memory import SaveMemoryTool, SearchMemoryTool, DeleteMemoryTool
from . import mcp_manager as mcp
from .tool import MAX_TOOL_RESULT_LENGTH

tool_registry = {
    "read_file": ReadFileTool,
    "write_file": WriteFileTool,
    "bash": BashTool,
    "web_fetch": WebFetchTool,
    "get_skills_dir": GetSkillsDirTool,
    "todo_add": TodoAddTool,
    "todo_list": TodoListTool,
    "todo_update": TodoUpdateTool,
    "todo_clear": TodoClearTool,
    "calculator": CalculatorTool,
    "hackernews": HackerNewsTool,
    "websearch_text": WebSearchText,
    "websearch_images": WebSearchImages,
    "websearch_videos": WebSearchVideos,
    "websearch_news": WebSearchNews,
    "websearch_books": WebSearchBooks,
    "list_scheduled_tasks": ListScheduledTasks,
    "add_scheduled_task": AddScheduledTask,
    "update_scheduled_task": UpdateScheduledTask,
    "remove_scheduled_task": RemoveScheduledTask,
    "get_scheduled_task_output": GetScheduledTaskOutput,
    "get_city_state": GetCityState,
    "get_datetime": GetDateTime,
    "helper_agent": HelperAgentTool,
    "save_memory": SaveMemoryTool,
    "search_memory": SearchMemoryTool,
    "delete_memory": DeleteMemoryTool,
}

all_tool_specs = [tool.spec() for tool in tool_registry.values()]
_HELPER_AGENT_TOOLS = {
    "read_file",
    "write_file",
    "bash",
    "web_fetch",
    "get_skills_dir",
    "calculator",
    "hackernews",
    "websearch_text",
    "websearch_images",
    "websearch_videos",
    "websearch_news",
    "websearch_books",
    "get_city_state",
    "get_datetime",
    "list_scheduled_tasks",
    "get_scheduled_task_output",
    # Read-only: scheduled tasks can use memories but not change them.
    "search_memory",
}

helper_tool_specs = [
    tool.spec() for k, tool in tool_registry.items() if k in _HELPER_AGENT_TOOLS
]


def get_all_tool_specs() -> list[dict]:
    """Built-in tool specs plus the specs of every connected MCP server's tools."""
    return all_tool_specs + mcp.mcp_manager.get_tool_specs()


async def run_tool_async(tool_name: str, tool_args: dict) -> str:
    """Run a tool by name: MCP tools via the MCP manager, the rest via run_tool()."""
    if mcp.mcp_manager.is_mcp_tool(tool_name):
        blocked = await _wait_for_unfinished()
        if blocked:
            return blocked
        return await mcp.mcp_manager.call_tool(tool_name, tool_args)
    return await run_tool(tool_name=tool_name, tool_args=tool_args)


# Worker threads of sync tool calls whose caller was cancelled (Ctrl+C), by
# tool name. A thread can't be stopped, so these keep running; every new tool
# call waits for them first (see _wait_for_unfinished), so their side effects
# can't overlap a later tool's, while the cancelled turn itself ends at once.
_unfinished: dict[asyncio.Future, str] = {}


def unfinished_tools() -> list[str]:
    """Names of tools from cancelled calls that are still running in a thread."""
    return sorted(set(_unfinished.values()))


# How long a new tool call waits for such threads before giving up.
UNFINISHED_WAIT_SECS = 60.0


async def _wait_for_unfinished() -> str:
    """Wait for cancelled calls' threads; returns "" or, after
    UNFINISHED_WAIT_SECS, an error for the model (a hung thread must not stall
    every later tool call, in every channel)."""
    deadline = asyncio.get_running_loop().time() + UNFINISHED_WAIT_SECS
    while _unfinished:
        left = deadline - asyncio.get_running_loop().time()
        if left <= 0:
            names = ", ".join(unfinished_tools())
            log.warning(
                "Tool call refused: %s from a cancelled call still running", names
            )
            return (
                f"Error: not run, because {names} from an interrupted call is still "
                f"running after {UNFINISHED_WAIT_SECS:.0f}s and running this now "
                "could overlap it. Try again later."
            )
        await asyncio.wait(set(_unfinished), timeout=left)
    return ""


def _forget_unfinished(future: asyncio.Future) -> None:
    _unfinished.pop(future, None)
    if not future.cancelled():
        future.exception()  # retrieved, so asyncio doesn't warn about it


# Sync tools run one at a time, as they did when they ran on the event loop:
# parallel calls from one model turn (two write_file calls to one path, a
# read racing a write) must not overlap. One lock per event loop.
_thread_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _get_thread_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _thread_locks.get(loop)
    if lock is None:
        lock = _thread_locks[loop] = asyncio.Lock()
    return lock


def _start_daemon_thread(name: str, func, tool_args: dict) -> asyncio.Future:
    """Run ``func`` in a new daemon thread; returns a future for its result.

    Like ``asyncio.to_thread`` (the context is copied, so the todo list and
    other ContextVars carry over), but on a daemon thread: if a cancelled
    tool hangs, quitting doesn't wait for it, whereas the default executor's
    threads are joined at interpreter exit.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    context = contextvars.copy_context()

    def settle(setter, value) -> None:
        if not future.done():
            setter(value)

    def runner() -> None:
        try:
            result = context.run(func, **tool_args)
        except BaseException as e:  # pylint: disable=broad-exception-caught  # handed to the awaiting task
            outcome = (future.set_exception, e)
        else:
            outcome = (future.set_result, result)
        try:
            loop.call_soon_threadsafe(settle, *outcome)
        except RuntimeError:
            pass  # the loop is gone (we're quitting): nobody is waiting

    threading.Thread(target=runner, name=f"tool-{name}", daemon=True).start()
    return future


async def _run_in_thread(name: str, func, tool_args: dict):
    """Run a sync tool in a worker thread, one such tool at a time.

    If the caller is cancelled, the thread is left to finish on its own and
    recorded in ``_unfinished``, so the cancellation (e.g. Ctrl+C in the TUI)
    takes effect at once; the next tool call waits for it there.
    """
    async with _get_thread_lock():
        # Check again under the lock: a call that was holding it may have been
        # cancelled (its thread still running) while this one waited for it.
        blocked = await _wait_for_unfinished()
        if blocked:
            return blocked
        future = _start_daemon_thread(name, func, tool_args)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if not future.done():
                _unfinished[future] = name
                future.add_done_callback(_forget_unfinished)
            raise


async def run_tool(tool_name: str, tool_args: dict) -> str:
    """Run a built-in tool by name and return its result as a (truncated) string.

    Errors are returned as text so the model can see them. The working
    directory is restored afterwards, in case the tool changed it.
    Synchronous tools (file and web tools, search, …) run in a worker
    thread, one at a time, so a slow one doesn't freeze the event loop: the
    other channels, the TUI's screen and Ctrl+C keep working meanwhile. Tools
    with ``runs_in_thread = False`` run on the loop instead.
    """
    blocked = await _wait_for_unfinished()  # a cancelled call's thread first
    if blocked:
        return blocked
    original_cwd = os.getcwd()

    try:
        tool = tool_registry[tool_name]
        func = tool.call
        if inspect.iscoroutinefunction(func):
            result = await func(**tool_args)
        elif tool.runs_in_thread:
            result = await _run_in_thread(tool_name, func, tool_args)
        else:
            result = func(**tool_args)
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
        log.error("Error running tool %s: %s", tool_name, str(e))
        result = f"Error running tool {tool_name}: {str(e)}"
    finally:
        os.chdir(original_cwd)

    if not isinstance(result, str):
        result = json.dumps(result)

    return trunc_str_with_ellipsis(MAX_TOOL_RESULT_LENGTH, result)
