import json
import pytest
from unittest.mock import AsyncMock, patch

from app.core.mcp_manager import MCPManager


def make_manager(configs=None, config_path=None):
    m = MCPManager()
    m._server_configs = configs if configs is not None else {}
    m._config_path = config_path
    return m


# --- enable_server / disable_server ---

@pytest.mark.asyncio
async def test_enable_unknown_server_raises():
    m = make_manager()
    with pytest.raises(ValueError):
        await m.enable_server("ghost")


@pytest.mark.asyncio
async def test_disable_unknown_server_raises():
    m = make_manager()
    with pytest.raises(ValueError):
        await m.disable_server("ghost")


@pytest.mark.asyncio
async def test_enable_connects_and_clears_disabled_flag():
    m = make_manager({"srv": {"command": "x", "disabled": True}})
    with patch.object(MCPManager, "_connect_server", new=AsyncMock()) as conn:
        status = await m.enable_server("srv")
    conn.assert_called_once()
    assert status["disabled"] is False
    assert "disabled" not in m._server_configs["srv"]


@pytest.mark.asyncio
async def test_enable_already_connected_skips_connect():
    m = make_manager({"srv": {"command": "x"}})
    m._clients["srv"] = AsyncMock()
    with patch.object(MCPManager, "_connect_server", new=AsyncMock()) as conn:
        status = await m.enable_server("srv")
    conn.assert_not_called()
    assert status["connected"] is True


@pytest.mark.asyncio
async def test_disable_disconnects_and_drops_tools():
    m = make_manager({"srv": {"command": "x"}, "other": {"command": "y"}})
    client = AsyncMock()
    m._clients["srv"] = client
    m._specs["srv__tool_a"] = {"type": "function", "function": {"name": "srv__tool_a"}}
    m._specs["other__tool"] = {"type": "function", "function": {"name": "other__tool"}}

    status = await m.disable_server("srv")

    client.__aexit__.assert_called_once()
    assert "srv" not in m._clients
    assert "srv__tool_a" not in m._specs
    assert "other__tool" in m._specs
    assert status["disabled"] is True
    assert status["connected"] is False
    assert status["tool_count"] == 0


@pytest.mark.asyncio
async def test_disable_when_not_connected_does_not_raise():
    m = make_manager({"srv": {"command": "x"}})
    status = await m.disable_server("srv")
    assert status["disabled"] is True


# --- persistence ---

@pytest.mark.asyncio
async def test_disable_persists_to_config_file(tmp_path):
    cfg_file = tmp_path / "mcp_servers.json"
    cfg_file.write_text(json.dumps({"mcpServers": {"srv": {"command": "x"}}}))
    m = make_manager({"srv": {"command": "x"}}, config_path=cfg_file)

    await m.disable_server("srv")

    data = json.loads(cfg_file.read_text())
    assert data["mcpServers"]["srv"]["disabled"] is True


@pytest.mark.asyncio
async def test_enable_removes_disabled_flag_from_config_file(tmp_path):
    cfg_file = tmp_path / "mcp_servers.json"
    cfg_file.write_text(json.dumps({"mcpServers": {"srv": {"command": "x", "disabled": True}}}))
    m = make_manager({"srv": {"command": "x", "disabled": True}}, config_path=cfg_file)

    with patch.object(MCPManager, "_connect_server", new=AsyncMock()):
        await m.enable_server("srv")

    data = json.loads(cfg_file.read_text())
    assert "disabled" not in data["mcpServers"]["srv"]


@pytest.mark.asyncio
async def test_persist_missing_config_file_does_not_raise(tmp_path):
    m = make_manager({"srv": {"command": "x"}}, config_path=tmp_path / "nope.json")
    await m.disable_server("srv")  # must not raise


@pytest.mark.asyncio
async def test_no_config_path_skips_persistence():
    m = make_manager({"srv": {"command": "x"}})
    await m.disable_server("srv")  # must not raise


# --- get_server_status ---

def test_get_server_status_reports_all_fields():
    m = make_manager({"srv": {"url": "http://localhost:1234/sse"}})
    m._clients["srv"] = object()
    m._specs["srv__tool_a"] = {}
    status = m.get_server_status()
    assert status == [{
        "name": "srv",
        "connected": True,
        "disabled": False,
        "transport": "url",
        "target": "http://localhost:1234/sse",
        "tool_count": 1,
    }]
