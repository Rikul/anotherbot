import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.mcp_manager import MCPManager, initialize_mcp
from app.core.tool import MAX_TOOL_RESULT_LENGTH


def _tool(name, description="d", schema=None):
    return SimpleNamespace(name=name, description=description, inputSchema=schema)


# --- _result_to_str ---

def test_result_to_str_joins_text_parts():
    result = SimpleNamespace(content=[SimpleNamespace(text="a"), SimpleNamespace(), SimpleNamespace(text="b")])
    assert MCPManager()._result_to_str(result) == "a\nb"


def test_result_to_str_uses_json_data_when_no_text():
    result = SimpleNamespace(content=[], data={"x": 1})
    assert MCPManager()._result_to_str(result) == json.dumps({"x": 1})


def test_result_to_str_falls_back_to_str_for_unserialisable_data():
    result = SimpleNamespace(content=None, data={1, 2})  # sets aren't JSON-serialisable
    assert MCPManager()._result_to_str(result) == str(result)


def test_result_to_str_truncates():
    result = SimpleNamespace(content=[SimpleNamespace(text="x" * (MAX_TOOL_RESULT_LENGTH + 50))])
    assert len(MCPManager()._result_to_str(result)) <= MAX_TOOL_RESULT_LENGTH + 10


# --- connecting servers ---

@pytest.mark.asyncio
async def test_initialize_registers_namespaced_tools_and_status():
    client = MagicMock()
    client.__aenter__ = AsyncMock()
    client.list_tools = AsyncMock(return_value=[_tool("read"), _tool("write")])
    mgr = MCPManager()
    with patch.object(MCPManager, "_build_client", return_value=client):
        await mgr.initialize({"mem": {"command": "npx"}, "off": {"command": "x", "disabled": True}})
    assert [s["function"]["name"] for s in mgr.get_tool_specs()] == ["mem__read", "mem__write"]
    assert mgr.is_mcp_tool("mem__read")
    status = {s["name"]: s for s in mgr.get_server_status()}
    assert status["mem"]["connected"] and status["mem"]["tool_count"] == 2
    assert status["off"]["disabled"] and not status["off"]["connected"]


@pytest.mark.asyncio
async def test_bad_server_does_not_stop_others():
    good = MagicMock()
    good.__aenter__ = AsyncMock()
    good.list_tools = AsyncMock(return_value=[_tool("t")])

    def build(cfg):
        if cfg["command"] == "bad":
            raise RuntimeError("boom")
        return good

    mgr = MCPManager()
    with patch.object(MCPManager, "_build_client", side_effect=build):
        await mgr.initialize({"bad": {"command": "bad"}, "ok": {"command": "ok"}, "a__b": {"command": "ok"}})
    assert [s["function"]["name"] for s in mgr.get_tool_specs()] == ["ok__t"]


@pytest.mark.asyncio
async def test_failed_tool_listing_closes_client():
    client = MagicMock()
    client.__aenter__ = AsyncMock()
    client.__aexit__ = AsyncMock()
    client.list_tools = AsyncMock(side_effect=RuntimeError("nope"))
    mgr = MCPManager()
    with patch.object(MCPManager, "_build_client", return_value=client):
        await mgr.initialize({"s": {"command": "c"}})
    client.__aexit__.assert_awaited_once()
    assert mgr.get_tool_specs() == []


# --- calling tools ---

@pytest.mark.asyncio
async def test_call_tool_errors_are_returned_as_text():
    mgr = MCPManager()
    assert "not namespaced" in await mgr.call_tool("plain", {})
    assert "not connected" in await mgr.call_tool("srv__t", {})
    client = MagicMock()
    client.call_tool = AsyncMock(side_effect=RuntimeError("kaput"))
    mgr._clients["srv"] = client
    assert await mgr.call_tool("srv__t", {}) == "Error calling MCP tool srv__t: kaput"


@pytest.mark.asyncio
async def test_shutdown_continues_after_close_error():
    a, b = MagicMock(), MagicMock()
    a.__aexit__ = AsyncMock(side_effect=RuntimeError("x"))
    b.__aexit__ = AsyncMock()
    mgr = MCPManager()
    mgr._clients = {"a": a, "b": b}
    await mgr.shutdown()
    b.__aexit__.assert_awaited_once()
    assert mgr._clients == {}


# --- initialize_mcp config file handling ---

@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not json{{", b"\xff\xfe\x00bad", b"[1, 2]", b'{"mcpServers": [1]}'])
async def test_initialize_mcp_bad_files_are_logged_not_raised(tmp_path, content):
    (tmp_path / "mcp_servers.json").write_bytes(content)
    mgr = MagicMock()
    mgr.initialize = AsyncMock()
    with patch("app.core.mcp_manager.config") as cfg, patch("app.core.mcp_manager.mcp_manager", mgr):
        cfg.PROJECT_HOME = str(tmp_path)
        await initialize_mcp()
    mgr.initialize.assert_not_called()
