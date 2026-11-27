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
        assert _names(HelperAgent().tool_specs()) == _HELPER_AGENT_TOOLS


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


# ---------------------------------------------------------------------------
# Agent tool modes (/default, /yolo, /readonly, /toolset)
# ---------------------------------------------------------------------------

from app.channels.commands import build_command_registry  # noqa: E402
from app.core.agent import Agent  # noqa: E402
from app.core.toolsets import AGENT_MODES, DEFAULT_MODE, mode_name  # noqa: E402


class _ModeAgent(Agent):
    """Just enough of a conversation agent for the command registry."""

    channel_str = "cli"
    conversation_id = 1
    store = MagicMock()

    def switch_conversation(self, conv):  # pragma: no cover - unused
        pass

    async def agent_loop(self, message, metadata=None):  # pragma: no cover
        return ""


@pytest.fixture
def mode_agent():
    with patch("app.core.agent.Client"):
        yield _ModeAgent()


def test_agents_default_to_every_tool(mode_agent):
    assert DEFAULT_MODE == "default"
    assert mode_agent.toolsets == AGENT_MODES["default"] == ("all",)
    assert not mode_agent.auto_approve
    assert mode_agent.mode_name() == "default"
    mcp_spec = {"type": "function", "function": {"name": "srv__x"}}
    mgr = MagicMock()
    mgr.get_tool_specs.return_value = [mcp_spec]
    with patch("app.core.mcp_manager.mcp_manager", mgr):
        names = _names(mode_agent.tool_specs())
    assert names == set(tool_registry) | {"srv__x"}


def test_readonly_mode_has_no_writes_shell_web_or_mcp(mode_agent):
    mode_agent.set_toolsets(AGENT_MODES["readonly"])
    tools, servers = resolve_toolset(*mode_agent.toolsets)
    assert servers == ()
    assert resolve_toolset("read_only")[0] <= tools
    for name in ("write_file", "bash", "web_fetch", "save_memory", "helper_agent"):
        assert name not in tools
    assert mode_name(mode_agent.toolsets) == "readonly"


def test_set_toolsets_rejects_unknown(mode_agent):
    with pytest.raises(ValueError):
        mode_agent.set_toolsets(("nope",))
    assert mode_agent.toolsets == AGENT_MODES["default"]


@pytest.mark.asyncio
async def test_agent_refuses_tool_outside_the_turns_specs(mode_agent):
    mode_agent._allowed_tools = {"read_file"}
    call = SimpleNamespace(
        id="1", function=SimpleNamespace(name="bash", arguments='{"command": "ls"}')
    )
    with patch.object(tool_calls, "run_tool_async", AsyncMock()) as run:
        result = await mode_agent.handle_tool_call(call)
    run.assert_not_called()
    assert "not available" in result


@pytest.mark.asyncio
async def test_loop_gives_model_only_the_current_tools(mode_agent):
    mode_agent.set_toolsets(AGENT_MODES["readonly"])
    from tests.test_agent import make_mock_client

    mode_agent.client = make_mock_client(content="done")
    await mode_agent._loop([{"role": "user", "content": "hi"}], mode_agent.tool_specs())
    sent = mode_agent.client.chat.completions.create.call_args.kwargs["tools"]
    assert "bash" not in _names(sent) and "read_file" in _names(sent)
    assert mode_agent._allowed_tools == _names(sent)


@pytest.mark.asyncio
async def test_mode_commands_switch_toolsets(mode_agent):
    registry = build_command_registry(mode_agent)
    reply = await registry.execute("readonly")
    assert mode_agent.toolsets == AGENT_MODES["readonly"]
    assert reply.startswith("Tools: readonly")
    assert "Tools:        readonly" in await registry.execute("status")
    assert not mode_agent.auto_approve


@pytest.mark.asyncio
async def test_yolo_turns_prompts_off_and_other_modes_back_on(mode_agent):
    registry = build_command_registry(mode_agent)
    reply = await registry.execute("yolo")
    assert mode_agent.toolsets == ("all",) and mode_agent.auto_approve
    assert "no permission prompts" in reply
    assert mode_agent.mode_name() == "yolo"
    assert "Tools:        yolo" in await registry.execute("status")
    await registry.execute("readonly")
    assert not mode_agent.auto_approve
    await registry.execute("yolo")
    await registry.execute("default")
    assert mode_agent.toolsets == ("all",) and not mode_agent.auto_approve
    assert mode_agent.mode_name() == "default"


def test_cli_dash_y_reads_as_yolo():
    with patch("app.core.agent.Client"):
        agent = _ModeAgent()
    agent.auto_approve = True  # what CliAgent(auto_approve=True) sets
    assert agent.mode_name() == "yolo"
    agent.set_toolsets(AGENT_MODES["readonly"])
    assert agent.mode_name() == "readonly"  # the TUI shows auto-approve itself


@pytest.mark.asyncio
async def test_toolset_command(mode_agent):
    registry = build_command_registry(mode_agent)
    listing = await registry.execute("toolset")
    assert "Current: default" in listing and "read_only —" in listing
    assert (await registry.execute("toolset", "read_only, web")).startswith(
        "Tools: read_only+web"
    )
    assert mode_agent.toolsets == ("read_only", "web")
    assert "Unknown toolset" in await registry.execute("toolset", "bogus")
    assert mode_agent.toolsets == ("read_only", "web")
