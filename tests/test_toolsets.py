from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import tool_calls
from app.core.helper_agent import HelperAgent
from app.core.tool_calls import (
    _HELPER_AGENT_TOOLS,
    resolve_toolset,
    tool_registry,
    toolset_specs,
)
from app.core.toolsets import TOOLSETS
from app.tools.helper_agent import HELPER_TOOLSET_CHOICES, HelperAgentTool


def _names(specs):
    return {s["function"]["name"] for s in specs}


def test_every_toolset_resolves():
    for name in TOOLSETS:
        tools, _ = resolve_toolset(name)
        assert tools <= set(tool_registry)


def test_read_only_toolset_matches_read_only_flag():
    flagged = {k for k, t in tool_registry.items() if t.read_only}
    assert resolve_toolset("read_only")[0] == flagged


def test_helper_toolset_is_the_old_allowlist():
    assert _HELPER_AGENT_TOOLS == {
        "read_file", "write_file", "bash", "list_dir", "glob", "grep",
        "git_status", "git_diff", "git_log", "web_fetch", "use_skill",
        "calculator", "hackernews", "websearch_text", "websearch_images",
        "websearch_videos", "websearch_news", "websearch_books",
        "get_city_state", "get_datetime", "list_scheduled_tasks",
        "get_scheduled_task_output", "search_memory",
    }  # fmt: skip
    for forbidden in ("add_scheduled_task", "helper_agent", "save_memory"):
        assert forbidden not in _HELPER_AGENT_TOOLS


def test_nested_toolsets_combine():
    tools, servers = resolve_toolset("read_only", "shell")
    assert "bash" in tools and "read_file" in tools
    assert servers == ()


def test_unknown_toolset_raises():
    with pytest.raises(ValueError, match="Unknown toolset"):
        resolve_toolset("nope")


def test_unknown_tool_raises():
    with patch.dict(TOOLSETS, {"bad": ("no_such_tool",)}):
        with pytest.raises(ValueError, match="unknown tool no_such_tool"):
            resolve_toolset("bad")


def test_cycle_raises():
    with patch.dict(TOOLSETS, {"a": ("@b",), "b": ("@a",)}):
        with pytest.raises(ValueError, match="cycle"):
            resolve_toolset("a")


def test_mcp_member_includes_server_tools():
    spec = {"type": "function", "function": {"name": "scan__ports"}}
    mgr = MagicMock()
    mgr.get_tools_for_server.return_value = [spec]
    with (
        patch.dict(TOOLSETS, {"security": ("@read_only", "mcp:scan")}),
        patch("app.core.mcp_manager.mcp_manager", mgr),
    ):
        assert resolve_toolset("security")[1] == ("scan",)
        specs = toolset_specs("security")
    mgr.get_tools_for_server.assert_called_once_with("scan")
    assert spec in specs
    assert "read_file" in _names(specs) and "bash" not in _names(specs)


def test_toolset_specs_keep_registry_order():
    order = [k for k in tool_registry if k in resolve_toolset("files")[0]]
    assert [s["function"]["name"] for s in toolset_specs("files")] == order


def test_helper_agent_default_toolset():
    with patch("app.core.agent.Client"):
        assert _names(HelperAgent().tool_specs) == _HELPER_AGENT_TOOLS


@pytest.mark.asyncio
async def test_helper_agent_refuses_tool_outside_its_toolset():
    with patch("app.core.agent.Client"):
        agent = HelperAgent(toolsets=("read_only",))
    call = SimpleNamespace(
        id="1", function=SimpleNamespace(name="bash", arguments='{"command": "ls"}')
    )
    with patch.object(tool_calls, "run_tool", AsyncMock()) as run:
        result = await agent.handle_tool_call(call)
    run.assert_not_called()
    assert "not available" in result


def test_helper_agent_tool_choices_never_widen_helper():
    for name in HELPER_TOOLSET_CHOICES:
        tools, servers = resolve_toolset(name)
        assert tools <= _HELPER_AGENT_TOOLS, name
        assert servers == (), name
    enum = HelperAgentTool.spec()["function"]["parameters"]["properties"]["toolset"]
    assert enum["enum"] == list(HELPER_TOOLSET_CHOICES)


@pytest.mark.asyncio
async def test_helper_agent_tool_passes_toolset():
    helper = AsyncMock()
    helper.run = AsyncMock(return_value="ok")
    with patch("app.core.helper_agent.HelperAgent", return_value=helper) as cls:
        assert await HelperAgentTool.call(prompt="p", toolset="read_only") == "ok"
    cls.assert_called_once_with(system_prompt=None, toolsets=("read_only",))


@pytest.mark.asyncio
async def test_helper_agent_tool_rejects_unknown_toolset():
    with patch("app.core.helper_agent.HelperAgent") as cls:
        result = await HelperAgentTool.call(prompt="p", toolset="schedule")
    cls.assert_not_called()
    assert result.startswith("Error: unknown toolset")
