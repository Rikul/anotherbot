
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.core.tool_calls import run_tool, tool_registry, all_tool_specs, get_all_tool_specs, run_tool_async


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
    mcp_spec = {"type": "function", "function": {"name": "srv__tool", "description": "", "parameters": {}}}
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
