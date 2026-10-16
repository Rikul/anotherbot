import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.channels.commands import BotCommand, CommandRegistry, status_cmd, help_cmd, model_cmd, trace_cmd


def make_registry(*extra: BotCommand) -> CommandRegistry:
    r = CommandRegistry()
    r.register(BotCommand("status", "Show bot status.", status_cmd))
    r.register(BotCommand("help", "Show this help message.", help_cmd(r)))
    for cmd in extra:
        r.register(cmd)
    return r


# --- BotCommand ---

def test_botcommand_stores_fields():
    handler = AsyncMock(return_value="hi")
    cmd = BotCommand("test", "A test command.", handler)
    assert cmd.name == "test"
    assert cmd.description == "A test command."
    assert cmd.handler is handler


# --- CommandRegistry.list ---

def test_registry_list_empty_by_default():
    assert CommandRegistry().list() == []


def test_registry_list_returns_registered_commands():
    r = make_registry()
    names = {c.name for c in r.list()}
    assert {"status", "help"} <= names


def test_registry_register_overwrites_same_name():
    r = CommandRegistry()
    r.register(BotCommand("x", "first", AsyncMock(return_value="a")))
    r.register(BotCommand("x", "second", AsyncMock(return_value="b")))
    assert len(r.list()) == 1
    assert r.list()[0].description == "second"


# --- CommandRegistry.execute ---

@pytest.mark.asyncio
async def test_execute_returns_none_for_unknown_command():
    assert await CommandRegistry().execute("nope") is None


@pytest.mark.asyncio
async def test_execute_calls_handler_and_returns_result():
    handler = AsyncMock(return_value="pong")
    r = CommandRegistry()
    r.register(BotCommand("ping", "Ping.", handler))
    result = await r.execute("ping")
    assert result == "pong"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_execute_returns_error_string_on_exception():
    async def boom(args=""):
        raise RuntimeError("oops")
    r = CommandRegistry()
    r.register(BotCommand("bad", "Broken.", boom))
    result = await r.execute("bad")
    assert isinstance(result, str)
    assert result  # non-empty


@pytest.mark.asyncio
async def test_execute_passes_args_to_handler():
    handler = AsyncMock(return_value="ok")
    r = CommandRegistry()
    r.register(BotCommand("cmd", "Test.", handler))
    await r.execute("cmd", "some args")
    handler.assert_called_once_with("some args")


# --- status_cmd ---

@pytest.mark.asyncio
async def test_status_includes_model_name():
    with patch("app.core.runtime._store", {"model": "my-test-model"}):
        result = await status_cmd()
    assert "my-test-model" in result


@pytest.mark.asyncio
async def test_status_includes_uptime():
    with patch("app.config._config", {"model": "m"}):
        result = await status_cmd()
    assert "Uptime" in result


# --- help_cmd ---

@pytest.mark.asyncio
async def test_help_handler_lists_all_registered_commands():
    r = CommandRegistry()
    r.register(BotCommand("foo", "Foo command.", AsyncMock(return_value="")))
    r.register(BotCommand("bar", "Bar command.", AsyncMock(return_value="")))
    r.register(BotCommand("help", "Help.", help_cmd(r)))
    result = await r.execute("help")
    assert "/foo" in result
    assert "/bar" in result
    assert "/help" in result


@pytest.mark.asyncio
async def test_help_handler_reflects_commands_registered_after_creation():
    r = CommandRegistry()
    r.register(BotCommand("help", "Help.", help_cmd(r)))
    r.register(BotCommand("late", "Registered after help.", AsyncMock(return_value="")))
    result = await r.execute("help")
    assert "/late" in result


# --- model_cmd ---

@pytest.mark.asyncio
async def test_model_cmd_returns_current_model_when_no_args():
    with patch("app.core.runtime._store", {"model": "deepseek/v3"}):
        result = await model_cmd("")
    assert "deepseek/v3" in result


@pytest.mark.asyncio
async def test_model_cmd_sets_model():
    mock_store = {"model": "old-model"}
    with patch("app.core.runtime._store", mock_store):
        result = await model_cmd("new-model")
    assert "new-model" in result
    assert mock_store["model"] == "new-model"


# --- trace_cmd ---

@pytest.mark.asyncio
async def test_trace_cmd_shows_off_when_no_args():
    with patch("app.core.runtime._store", {"trace": False, "tracedir": Path("/tmp/traces")}):
        result = await trace_cmd("")
    assert "off" in result


@pytest.mark.asyncio
async def test_trace_cmd_shows_on_when_no_args():
    with patch("app.core.runtime._store", {"trace": True, "tracedir": Path("/tmp/traces")}):
        result = await trace_cmd("")
    assert "on" in result


@pytest.mark.asyncio
async def test_trace_cmd_on_enables_tracing():
    mock_store = {"trace": False, "tracedir": Path("/tmp/traces")}
    with patch("app.core.runtime._store", mock_store):
        result = await trace_cmd("on")
    assert mock_store["trace"] is True
    assert "on" in result.lower()


@pytest.mark.asyncio
async def test_trace_cmd_off_disables_tracing():
    mock_store = {"trace": True, "tracedir": Path("/tmp/traces")}
    with patch("app.core.runtime._store", mock_store):
        result = await trace_cmd("off")
    assert mock_store["trace"] is False
    assert "off" in result.lower()


@pytest.mark.asyncio
async def test_trace_cmd_off_closes_open_trace_files():
    with patch("app.core.runtime._store", {"trace": True, "tracedir": Path("/tmp/traces")}), \
         patch("app.infra.tracer.close_all") as mock_close_all:
        await trace_cmd("off")
    mock_close_all.assert_called_once()


@pytest.mark.asyncio
async def test_trace_cmd_includes_tracedir_in_response():
    with patch("app.core.runtime._store", {"trace": False, "tracedir": Path("/my/traces")}):
        result = await trace_cmd("on")
    assert "/my/traces" in result


# --- status_cmd tracing line ---

@pytest.mark.asyncio
async def test_status_includes_tracing_off():
    with patch("app.core.runtime._store", {"trace": False, "model": "m"}):
        result = await status_cmd()
    assert "Tracing" in result
    assert "off" in result


@pytest.mark.asyncio
async def test_status_shows_tracing_on_with_filename():
    mock_store = {"trace": True, "last_trace": "trace_06142026_103045.json", "model": "m"}
    with patch("app.core.runtime._store", mock_store):
        result = await status_cmd()
    assert "on" in result
    assert "trace_06142026_103045.json" in result


@pytest.mark.asyncio
async def test_status_shows_tracing_on_without_filename():
    with patch("app.core.runtime._store", {"trace": True, "model": "m"}):
        result = await status_cmd()
    assert "Tracing" in result
    assert "on" in result


# --- /mcp ---

def _mcp_manager_mock(statuses=None, specs=None, server_specs=None):
    from unittest.mock import MagicMock
    mgr = MagicMock()
    mgr.get_server_status.return_value = statuses or []
    mgr.get_tool_specs.return_value = specs or []
    mgr.get_tools_for_server.side_effect = lambda name: (server_specs or {}).get(name, [])
    return mgr


def _spec(name, desc=""):
    return {"type": "function", "function": {"name": name, "description": desc}}


async def _run_mcp(args, mgr):
    from unittest.mock import patch
    from app.channels.commands import mcp_cmd
    with patch("app.core.mcp_manager.mcp_manager", mgr):
        return await mcp_cmd()(args)


@pytest.mark.asyncio
async def test_mcp_no_servers_points_to_config_file():
    out = await _run_mcp("", _mcp_manager_mock())
    assert out.startswith("No MCP servers configured.")
    assert "mcp_servers.json" in out


@pytest.mark.asyncio
async def test_mcp_lists_server_status():
    statuses = [
        {"name": "mem", "disabled": False, "connected": True, "transport": "stdio", "target": "npx", "tool_count": 3},
        {"name": "off", "disabled": True, "connected": False, "transport": "http", "target": "url", "tool_count": 0},
        {"name": "down", "disabled": False, "connected": False, "transport": "stdio", "target": "x", "tool_count": 0},
    ]
    out = await _run_mcp("", _mcp_manager_mock(statuses=statuses))
    assert out.splitlines() == [
        "MCP servers (3):",
        "  mem — connected, stdio (npx), 3 tool(s)",
        "  off — disabled, http (url), 0 tool(s)",
        "  down — disconnected, stdio (x), 0 tool(s)",
    ]


@pytest.mark.asyncio
async def test_mcp_tools_lists_all_tools():
    specs = [_spec("mem__read", "Read memory"), _spec("mem__write")]
    out = await _run_mcp("tools", _mcp_manager_mock(specs=specs))
    assert out.splitlines() == ["MCP tools (2):", "  mem__read — Read memory", "  mem__write"]


@pytest.mark.asyncio
async def test_mcp_tools_empty():
    assert await _run_mcp("TOOLS", _mcp_manager_mock()) == "No MCP tools available."


@pytest.mark.asyncio
async def test_mcp_tools_for_server_uses_bare_names():
    mgr = _mcp_manager_mock(server_specs={"mem": [_spec("mem__read", "Read")]})
    out = await _run_mcp("tools mem", mgr)
    assert out.splitlines() == ["Tools for 'mem' (1):", "  read — Read"]


@pytest.mark.asyncio
async def test_mcp_tools_for_unknown_server():
    mgr = _mcp_manager_mock(statuses=[{"name": "mem"}])
    assert await _run_mcp("tools nope", mgr) == "Server 'nope' not found. Configured: mem"


@pytest.mark.asyncio
async def test_mcp_tools_for_server_without_tools():
    mgr = _mcp_manager_mock(statuses=[{"name": "mem"}])
    assert await _run_mcp("tools mem", mgr) == "Server 'mem' has no tools."


# --- /rename and rename_conversation ---

@pytest.mark.asyncio
async def test_rename_cmd_updates_cached_name_for_every_channel_with_it_active(tmp_path):
    from app.channels.commands import rename_conversation_cmd
    from app.infra.conversations import ConversationStore
    store = ConversationStore(db_path=tmp_path / "t.db")
    cid = store.create("telegram", "Old")
    other = store.create("web", "Other")
    runtime_store = {"conversation_id:telegram": cid, "conversation_id:web": cid,
                     "conversation_id:discord": other, "conversation_name:discord": "Other"}
    with patch("app.core.runtime._store", runtime_store):
        reply = await rename_conversation_cmd(store)(f"{cid}  New name ")
    assert reply == f'Conversation [{cid}] renamed to "New name"'
    assert store.get(cid)["name"] == "New name"
    assert runtime_store["conversation_name:telegram"] == "New name"
    assert runtime_store["conversation_name:web"] == "New name"
    assert runtime_store["conversation_name:discord"] == "Other"  # different conversation


@pytest.mark.asyncio
async def test_rename_cmd_reports_missing_conversation(tmp_path):
    from app.channels.commands import rename_conversation_cmd
    from app.infra.conversations import ConversationStore
    store = ConversationStore(db_path=tmp_path / "t.db")
    assert await rename_conversation_cmd(store)("99 Name") == "Conversation 99 not found"


# --- /delete ---

class _FakeAgent:
    def __init__(self, store, channel, active_id):
        self.store, self.channel_str, self.conversation_id = store, channel, active_id

    def switch_conversation(self, conv):
        self.conversation_id = conv["id"]


@pytest.mark.asyncio
async def test_delete_cmd_deletes_inactive_conversation(tmp_path):
    from app.channels.commands import delete_conversation_cmd
    from app.infra.conversations import ConversationStore
    store = ConversationStore(db_path=tmp_path / "t.db")
    active, other = store.create("web", "Active"), store.create("telegram", "Old chat")
    agent = _FakeAgent(store, "web", active)
    with patch("app.core.runtime._store", {"conversation_id:web": active}):
        reply = await delete_conversation_cmd(agent)(str(other))
    assert reply == f'Deleted conversation [{other}] "Old chat"'
    assert store.get(other) is None and agent.conversation_id == active


@pytest.mark.asyncio
async def test_delete_cmd_on_channels_last_conversation_starts_a_new_one(tmp_path):
    from app.channels.commands import delete_conversation_cmd
    from app.infra.conversations import ConversationStore
    store = ConversationStore(db_path=tmp_path / "t.db")
    active = store.create("web", "Active")
    agent = _FakeAgent(store, "web", active)
    with patch("app.core.runtime._store", {"conversation_id:web": active}):
        reply = await delete_conversation_cmd(agent)(str(active))
    assert store.get(active) is None
    assert agent.conversation_id != active
    new = store.get(agent.conversation_id)
    assert new["channel"] == "web" and new["name"] == "New Conversation"
    assert reply == (f'Deleted conversation [{active}] "Active". '
                     f"Started new conversation [{new['id']}] New Conversation")


@pytest.mark.asyncio
async def test_delete_cmd_on_own_active_switches_to_channels_most_recent(tmp_path):
    import time
    from app.channels.commands import delete_conversation_cmd
    from app.infra.conversations import ConversationStore
    store = ConversationStore(db_path=tmp_path / "t.db")
    recent = store.create("web", "Recent")
    older = store.create("web", "Older")
    active = store.create("web", "Active")
    time.sleep(0.01)
    store.touch(recent)  # most recently updated web conversation
    time.sleep(0.01)
    store.create("telegram", "Newest but telegram")
    agent = _FakeAgent(store, "web", active)
    with patch("app.core.runtime._store", {"conversation_id:web": active}):
        reply = await delete_conversation_cmd(agent)(str(active))
    assert reply == f'Deleted conversation [{active}] "Active". Switched to conversation [{recent}] Recent'
    assert agent.conversation_id == recent
    assert store.get(active) is None and store.get(older) is not None
    assert [c["name"] for c in store.list("web")] == ["Recent", "Older"]  # nothing new created


@pytest.mark.asyncio
async def test_delete_cmd_refuses_conversation_active_in_another_channel(tmp_path):
    from app.channels.commands import delete_conversation_cmd
    from app.infra.conversations import ConversationStore
    store = ConversationStore(db_path=tmp_path / "t.db")
    mine, theirs = store.create("web", "Mine"), store.create("telegram", "Theirs")
    agent = _FakeAgent(store, "web", mine)
    with patch("app.core.runtime._store", {"conversation_id:web": mine,
                                           "conversation_id:telegram": theirs}):
        reply = await delete_conversation_cmd(agent)(str(theirs))
    assert "active in the telegram channel" in reply
    assert store.get(theirs) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("args,expected", [("", "Usage: /delete <id>"),
                                           ("abc", "Invalid id: must be an integer."),
                                           ("99", "Conversation not found")])
async def test_delete_cmd_bad_args(tmp_path, args, expected):
    from app.channels.commands import delete_conversation_cmd
    from app.infra.conversations import ConversationStore
    store = ConversationStore(db_path=tmp_path / "t.db")
    agent = _FakeAgent(store, "web", store.create("web"))
    assert await delete_conversation_cmd(agent)(args) == expected


def test_delete_is_in_the_standard_registry():
    from unittest.mock import MagicMock
    from app.channels.commands import build_command_registry
    agent = MagicMock(channel_str="web")
    assert "delete" in [c.name for c in build_command_registry(agent).list()]
