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
from ..tools.fs_search import GlobTool, GrepTool, ListDirTool
from ..tools.git_tools import GitDiffTool, GitLogTool, GitStatusTool
from ..tools.web_fetch import WebFetchTool
from ..tools.skill import UseSkillTool
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
from .toolsets import TOOLSETS

tool_registry = {
    "read_file": ReadFileTool,
    "write_file": WriteFileTool,
    "bash": BashTool,
    "list_dir": ListDirTool,
    "glob": GlobTool,
    "grep": GrepTool,
    "git_status": GitStatusTool,
    "git_diff": GitDiffTool,
    "git_log": GitLogTool,
    "web_fetch": WebFetchTool,
    "use_skill": UseSkillTool,
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

MCP_PREFIX = "mcp:"


def resolve_toolset(*names: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """Expand toolsets into (built-in tool names, MCP server names).

    Each name is a toolset from ``TOOLSETS``. Raises ``ValueError`` for an
    unknown toolset or tool, or a toolset that includes itself.
    """
    tools: set[str] = set()
    servers: list[str] = []

    def expand(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            raise ValueError(f"Toolset cycle: {' -> '.join((*path, name))}")
        if name not in TOOLSETS:
            raise ValueError(f"Unknown toolset: {name}")
        for member in TOOLSETS[name]:
            if member.startswith("@"):
                expand(member[1:], (*path, name))
            elif member.startswith(MCP_PREFIX):
                server = member[len(MCP_PREFIX) :]
                if server not in servers:
                    servers.append(server)
            elif member in tool_registry:
                tools.add(member)
            else:
                raise ValueError(f"Toolset {name}: unknown tool {member}")

    for name in names:
        expand(name, ())
    return frozenset(tools), tuple(servers)


def toolset_specs(*names: str) -> list[dict]:
    """Tool specs for the given toolsets: built-ins in registry order, then
    the tools of any MCP servers they name (those connected right now)."""
    tools, servers = resolve_toolset(*names)
    specs = [tool.spec() for k, tool in tool_registry.items() if k in tools]
    for server in servers:
        specs.extend(mcp.mcp_manager.get_tools_for_server(server))
    return specs


# Check every toolset at import, so a typo fails at startup, not mid-turn.
for _name in TOOLSETS:
    resolve_toolset(_name)

_HELPER_AGENT_TOOLS = resolve_toolset("helper")[0]
helper_tool_specs = toolset_specs("helper")


def is_read_only(tool_name: str) -> bool:
    """True for a built-in tool marked ``read_only`` (never for MCP tools)."""
    tool = tool_registry.get(tool_name)
    return bool(tool and tool.read_only)


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
# When each of those calls was cancelled (loop time): the wait for a thread is
# measured from then, not restarted by every later call.
_unfinished_since: dict[asyncio.Future, float] = {}


def unfinished_tools() -> list[str]:
    """Names of tools from cancelled calls that are still running in a thread."""
    return sorted(set(_unfinished.values()))


# How long a new tool call waits for such threads before giving up.
UNFINISHED_WAIT_SECS = 60.0
# After this long (from its cancel) a thread that still hasn't returned is
# abandoned: it stops blocking tool calls, so a thread stuck forever (e.g. on
# a dead socket) can't disable every tool in every channel until a restart.
UNFINISHED_ABANDON_SECS = 300.0


async def _wait_for_unfinished() -> str:
    """Wait for cancelled calls' threads; returns "" or an error for the model.

    A thread gets UNFINISHED_WAIT_SECS from when its call was cancelled; once
    the oldest one has run past that, every call fails at once instead of
    each waiting the full time again (a hung thread must not stall every
    later tool call, in every channel).
    """
    loop = asyncio.get_running_loop()
    _abandon_stale(loop.time())
    while _unfinished:
        oldest = min(_unfinished_since.get(f, loop.time()) for f in _unfinished)
        left = oldest + UNFINISHED_WAIT_SECS - loop.time()
        if left <= 0:
            names = ", ".join(unfinished_tools())
            log.warning(
                "Tool call refused: %s from a cancelled call still running", names
            )
            return (
                f"Error: not run, because {names} from an interrupted call is still "
                f"running after {UNFINISHED_WAIT_SECS:.0f}s and running this now "
                "could overlap it. Try again later (it is given up on after "
                f"{UNFINISHED_ABANDON_SECS:.0f}s)."
            )
        await asyncio.wait(set(_unfinished), timeout=left)
    return ""


def _abandon_stale(now: float) -> None:
    """Stop tracking threads cancelled more than UNFINISHED_ABANDON_SECS ago."""
    for future, since in list(_unfinished_since.items()):
        if now - since >= UNFINISHED_ABANDON_SECS and not future.done():
            log.error(
                "Abandoning %s: still running %.0fs after its call was cancelled; "
                "tool calls run again (its side effects may still happen)",
                _unfinished.get(future, "?"),
                now - since,
            )
            _unfinished.pop(future, None)
            _unfinished_since.pop(future, None)


def _forget_unfinished(future: asyncio.Future) -> None:
    _unfinished.pop(future, None)
    _unfinished_since.pop(future, None)
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
        if future.done():
            return
        try:
            setter(value)
        except (TypeError, ValueError, RuntimeError) as e:
            # Python < 3.14 refuses to store StopIteration on a future (it
            # raises TypeError); the call must still complete, or it would
            # hang holding the tool lock.
            future.set_exception(RuntimeError(f"{value!r}: {e}"))

    def runner() -> None:
        try:
            result = context.run(func, **tool_args)
        except BaseException as e:  # pylint: disable=broad-exception-caught  # handed to the awaiting task
            if isinstance(e, StopIteration):
                e = RuntimeError(f"tool raised {e!r}")  # can't go on a future
            outcome = (future.set_exception, e)
        else:
            outcome = (future.set_result, result)
        try:
            loop.call_soon_threadsafe(settle, *outcome)
        except RuntimeError:
            pass  # the loop is gone (we're quitting): nobody is waiting

    threading.Thread(target=runner, name=f"tool-{name}", daemon=True).start()
    return future


async def _run_exclusive(func, tool_args: dict):
    """Run an async tool marked ``exclusive`` one at a time, under the same
    lock as threaded sync tools (and after any unfinished cancelled thread)."""
    async with _get_thread_lock():
        blocked = await _wait_for_unfinished()
        if blocked:
            return blocked
        return await func(**tool_args)


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
                _unfinished_since[future] = asyncio.get_running_loop().time()
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
        if inspect.iscoroutinefunction(func) and tool.exclusive:
            result = await _run_exclusive(func, tool_args)
        elif inspect.iscoroutinefunction(func):
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
