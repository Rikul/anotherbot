import pytest
from app.core.tool_calls import run_tool, tool_registry


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
