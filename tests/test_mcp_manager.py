from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.mcp_manager import MCPManager


def test_build_client_accepts_url_config():
    mgr = MCPManager()
    sentinel = object()
    with patch("app.core.mcp_manager.Client", return_value=sentinel) as mock_client:
        result = mgr._build_client({"url": "https://example.com/sse"})

    mock_client.assert_called_once_with("https://example.com/sse")
    assert result is sentinel


@pytest.mark.parametrize(
    ("cfg", "message"),
    [
        ({}, "stdio config requires a non-empty 'command' string"),
        ({"command": ""}, "stdio config requires a non-empty 'command' string"),
        ({"command": "python", "args": "server.py"}, "stdio config 'args' must be a list of strings"),
        ({"command": "python", "args": ["server.py", 1]}, "stdio config 'args' must be a list of strings"),
        ({"command": "python", "env": []}, "stdio config 'env' must be a mapping of strings to strings"),
        ({"command": "python", "env": {"OK": 1}}, "stdio config 'env' must be a mapping of strings to strings"),
        ({"command": "python", "cwd": ""}, "stdio config 'cwd' must be a non-empty string"),
        ({"command": "python", "cwd": 123}, "stdio config 'cwd' must be a non-empty string"),
    ],
)
def test_build_client_rejects_invalid_stdio_configs(cfg, message):
    mgr = MCPManager()

    with pytest.raises(ValueError, match=message):
        mgr._build_client(cfg)


@pytest.mark.asyncio
async def test_connect_server_logs_actionable_validation_error():
    mgr = MCPManager()

    with patch("app.core.mcp_manager.log.error") as error_log:
        await mgr._connect_server("bad", {})

    assert "non-empty 'command' string" in error_log.call_args.args[0]


@pytest.mark.asyncio
async def test_call_tool_routes_tool_names_with_double_underscores():
    mgr = MCPManager()
    client = MagicMock()
    client.call_tool = AsyncMock(return_value="ok")
    mgr._clients["srv"] = client

    result = await mgr.call_tool("srv__tool__name", {"x": 1})

    client.call_tool.assert_awaited_once_with("tool__name", {"x": 1})
    assert result == "ok"
