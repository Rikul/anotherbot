import json
import logging
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.cli.cli import input_loop
from app.main import main, initialize_mcp


# ---------------------------------------------------------------------------
# input_loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_input_loop_yields_user_input():
    """Test that input_loop yields user input"""
    with patch("builtins.input", side_effect=["hello", "world", EOFError()]):
        results = []
        async for value in input_loop():
            results.append(value)
    
    assert results == ["hello", "world"]


@pytest.mark.asyncio
async def test_input_loop_skips_empty_input():
    """Test that input_loop skips empty user input"""
    with patch("builtins.input", side_effect=["", "hello", "", "world", EOFError()]):
        results = []
        async for value in input_loop():
            results.append(value)
    
    assert results == ["hello", "world"]


@pytest.mark.asyncio
async def test_input_loop_stops_on_eof():
    """Test that input_loop stops on EOF"""
    with patch("builtins.input", side_effect=[EOFError()]):
        results = []
        async for value in input_loop():
            results.append(value)
    
    assert results == []


@pytest.mark.asyncio
async def test_input_loop_stops_on_keyboard_interrupt():
    """Test that input_loop stops on KeyboardInterrupt"""
    with patch("builtins.input", side_effect=[KeyboardInterrupt()]):
        results = []
        async for value in input_loop():
            results.append(value)
    
    assert results == []


# ---------------------------------------------------------------------------
# Helpers to patch the heavy dependencies used by main()
# ---------------------------------------------------------------------------

def _patch_main(argv, agent_mock=None):
    """Context-manager stack that patches sys.argv and the Agent class.

    argv should contain only the arguments (without a leading program name);
    the helper prepends a dummy program name automatically.
    """
    if agent_mock is None:
        agent_mock = MagicMock()
        agent_mock.agent_loop = AsyncMock()

    patches = [
        patch("sys.argv", ["prog", "cli"] + argv),
        patch("app.main.CliAgent", return_value=agent_mock),
        patch("app.main.input_loop", return_value=AsyncMock()),
    ]
    return patches, agent_mock


# ---------------------------------------------------------------------------
# main() – argument parsing & validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_main_calls_agent_loop_with_prompt():
    """Test that main calls agent_loop with the prompt"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    patches, _ = _patch_main(["-p", "say hello", "--no-repl"], agent_mock=agent_mock)

    for p in patches:
        p.start()
    try:
        await main()
    finally:
        for p in patches:
            p.stop()

    agent_mock.agent_loop.assert_called_once_with("say hello")


@pytest.mark.asyncio
async def test_main_no_repl_exits_after_first_agent_loop():
    """Test that main with --no-repl exits after first agent_loop"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    patches, _ = _patch_main(["-p", "hi", "--no-repl"], agent_mock=agent_mock)

    for p in patches:
        p.start()
    try:
        await main()
    finally:
        for p in patches:
            p.stop()

    assert agent_mock.agent_loop.call_count == 1


@pytest.mark.asyncio
async def test_main_silent_exits_after_first_agent_loop():
    """Test that main with --silent exits after first agent_loop"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    patches, _ = _patch_main(["-p", "hi", "--silent"], agent_mock=agent_mock)

    for p in patches:
        p.start()
    try:
        await main()
    finally:
        for p in patches:
            p.stop()

    assert agent_mock.agent_loop.call_count == 1


@pytest.mark.asyncio
async def test_main_silent_sets_log_level_to_warning():
    """Test that --silent sets log level to WARNING"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    patches, _ = _patch_main(["-p", "hi", "--silent"], agent_mock=agent_mock)

    for p in patches:
        p.start()
    try:
        await main()
    finally:
        for p in patches:
            p.stop()

    from app.infra.app_logging import log
    assert log.level == logging.WARNING


@pytest.mark.asyncio
async def test_main_silent_implies_auto_approve():
    """Test that --silent implies auto_approve=True"""
    MockAgent = MagicMock()
    MockAgent.return_value.agent_loop = AsyncMock()

    with patch("sys.argv", ["prog", "cli", "-p", "hi", "--silent"]), patch("app.main.CliAgent", MockAgent):
        await main()

    _, kwargs = MockAgent.call_args
    assert kwargs["auto_approve"] is True


@pytest.mark.asyncio
async def test_main_returns_early_when_max_iterations_is_zero():
    """Test that main returns early when max_iterations is 0"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    patches, _ = _patch_main(["-p", "hi", "--no-repl", "--max-iterations", "0"], agent_mock=agent_mock)

    for p in patches:
        p.start()
    try:
        await main()
    finally:
        for p in patches:
            p.stop()

    agent_mock.agent_loop.assert_not_called()


@pytest.mark.asyncio
async def test_main_returns_early_when_max_iterations_is_negative():
    """Test that main returns early when max_iterations is negative"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    patches, _ = _patch_main(["-p", "hi", "--no-repl", "--max-iterations", "-5"], agent_mock=agent_mock)

    for p in patches:
        p.start()
    try:
        await main()
    finally:
        for p in patches:
            p.stop()

    agent_mock.agent_loop.assert_not_called()


@pytest.mark.asyncio
async def test_main_repl_calls_agent_loop_for_each_input():
    """Test that main REPL calls agent_loop for each input"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()

    async def fake_input_loop():
        for msg in ["second", "third"]:
            yield msg

    with patch("sys.argv", ["prog", "cli", "-p", "first"]), \
         patch("app.main.CliAgent", return_value=agent_mock), \
         patch("app.main.input_loop", fake_input_loop):
        await main()

    assert agent_mock.agent_loop.call_count == 3
    agent_mock.agent_loop.assert_any_call("first")
    agent_mock.agent_loop.assert_any_call("second")
    agent_mock.agent_loop.assert_any_call("third")


# ---------------------------------------------------------------------------
# initialize_mcp
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initialize_mcp_no_op_when_file_missing(tmp_path):
    mock_mgr = MagicMock()
    mock_mgr.initialize = AsyncMock()
    with patch("app.main.config") as mock_cfg, \
         patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        mock_cfg.PROJECT_HOME = str(tmp_path)
        await initialize_mcp()
    mock_mgr.initialize.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_mcp_calls_initialize_with_server_dict(tmp_path):
    servers = {"mem": {"command": "npx", "args": ["-y", "@mcp/mem"]}}
    (tmp_path / "mcp_servers.json").write_text(json.dumps({"mcpServers": servers}))
    mock_mgr = MagicMock()
    mock_mgr.initialize = AsyncMock()
    with patch("app.main.config") as mock_cfg, \
         patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        mock_cfg.PROJECT_HOME = str(tmp_path)
        await initialize_mcp()
    mock_mgr.initialize.assert_called_once_with(servers)


@pytest.mark.asyncio
async def test_initialize_mcp_handles_malformed_json(tmp_path):
    (tmp_path / "mcp_servers.json").write_text("not json{{")
    mock_mgr = MagicMock()
    mock_mgr.initialize = AsyncMock()
    with patch("app.main.config") as mock_cfg, \
         patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        mock_cfg.PROJECT_HOME = str(tmp_path)
        await initialize_mcp()  # must not raise
    mock_mgr.initialize.assert_not_called()