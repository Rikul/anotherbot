import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.cli.cli import input_loop
from app.main import main
from app.core.mcp_manager import initialize_mcp


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
        patch("app.cli.cli.CliAgent", return_value=agent_mock),
        patch("app.cli.cli.input_loop", return_value=AsyncMock()),
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
    patches, _ = _patch_main(["-p", "say hello"], agent_mock=agent_mock)

    for p in patches:
        p.start()
    try:
        await main()
    finally:
        for p in patches:
            p.stop()

    agent_mock.agent_loop.assert_called_once_with("say hello")


@pytest.mark.asyncio
async def test_main_prompt_runs_once_and_skips_repl():
    """Test that -p runs the prompt once and never starts the REPL"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    input_loop_mock = MagicMock()

    with patch("sys.argv", ["prog", "cli", "-p", "hi"]), \
         patch("app.cli.cli.CliAgent", return_value=agent_mock), \
         patch("app.cli.cli.input_loop", input_loop_mock):
        await main()

    agent_mock.agent_loop.assert_called_once_with("hi")
    input_loop_mock.assert_not_called()


@pytest.mark.asyncio
async def test_main_quiet_disables_console_logging():
    """Test that --quiet sets up logging without the console handler"""
    setup_mock = MagicMock()
    with patch("sys.argv", ["prog", "cli", "-p", "hi", "--quiet"]), \
         patch("app.main.setup_logging", setup_mock), \
         patch("app.main.run_cli", AsyncMock()):
        await main()

    _, kwargs = setup_mock.call_args
    assert kwargs["console"] is False


@pytest.mark.asyncio
async def test_main_logs_to_console_by_default():
    """Test that without --quiet the console handler is enabled"""
    setup_mock = MagicMock()
    with patch("sys.argv", ["prog", "cli", "-p", "hi"]), \
         patch("app.main.setup_logging", setup_mock), \
         patch("app.main.run_cli", AsyncMock()):
        await main()

    _, kwargs = setup_mock.call_args
    assert kwargs["console"] is True


@pytest.mark.asyncio
async def test_main_background_logs_to_console():
    """The background subcommand has no --quiet flag and keeps console logging"""
    setup_mock = MagicMock()
    with patch("sys.argv", ["prog", "background"]), \
         patch("app.main.setup_logging", setup_mock), \
         patch("app.main.run_background_agent", AsyncMock()):
        await main()

    _, kwargs = setup_mock.call_args
    assert kwargs["console"] is True


@pytest.mark.asyncio
async def test_main_quiet_does_not_imply_auto_approve():
    """Test that --quiet alone leaves tool permission prompts on"""
    MockAgent = MagicMock()
    MockAgent.return_value.agent_loop = AsyncMock()

    with patch("sys.argv", ["prog", "cli", "-p", "hi", "--quiet"]), patch("app.cli.cli.CliAgent", MockAgent):
        await main()

    _, kwargs = MockAgent.call_args
    assert kwargs["auto_approve"] is False


@pytest.mark.asyncio
async def test_main_auto_approve_flag():
    """Test that -y passes auto_approve=True to the agent"""
    MockAgent = MagicMock()
    MockAgent.return_value.agent_loop = AsyncMock()

    with patch("sys.argv", ["prog", "cli", "-p", "hi", "-y"]), patch("app.cli.cli.CliAgent", MockAgent):
        await main()

    _, kwargs = MockAgent.call_args
    assert kwargs["auto_approve"] is True


def test_silent_flag_removed():
    """--silent was replaced by --quiet and -y"""
    from app.main import parse_args
    with patch("sys.argv", ["prog", "cli", "-p", "hi", "--silent"]), \
         pytest.raises(SystemExit):
        parse_args()


@pytest.mark.asyncio
async def test_main_returns_early_when_max_iterations_is_zero():
    """Test that main returns early when max_iterations is 0"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    patches, _ = _patch_main(["-p", "hi", "--max-iterations", "0"], agent_mock=agent_mock)

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
    patches, _ = _patch_main(["-p", "hi", "--max-iterations", "-5"], agent_mock=agent_mock)

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
    """Test that without -p the REPL calls agent_loop for each input"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()

    async def fake_input_loop():
        for msg in ["first", "second"]:
            yield msg

    with patch("sys.argv", ["prog", "cli"]), \
         patch("app.cli.cli.CliAgent", return_value=agent_mock), \
         patch("app.cli.cli.input_loop", fake_input_loop):
        await main()

    assert agent_mock.agent_loop.call_count == 2
    agent_mock.agent_loop.assert_any_call("first")
    agent_mock.agent_loop.assert_any_call("second")


@pytest.mark.asyncio
async def test_main_repl_dispatches_slash_commands(capsys):
    """Test that REPL slash commands go to the registry, not the agent loop"""
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()

    async def fake_input_loop():
        for msg in ["/nosuchcmd", "/help"]:
            yield msg

    with patch("sys.argv", ["prog", "cli"]), \
         patch("app.cli.cli.CliAgent", return_value=agent_mock), \
         patch("app.cli.cli.input_loop", fake_input_loop):
        await main()

    agent_mock.agent_loop.assert_not_called()
    out = capsys.readouterr().out
    assert "Unknown command: /nosuchcmd" in out
    assert "/status" in out


@pytest.mark.asyncio
async def test_main_forwards_cli_args_to_run_cli():
    """Test that main passes parsed CLI args to run_cli"""
    run_cli_mock = AsyncMock()
    with patch("sys.argv", ["prog", "cli", "-p", "hi", "-y", "-q", "-i", "7"]), \
         patch("app.main.run_cli", run_cli_mock):
        await main()

    run_cli_mock.assert_awaited_once_with(
        max_iterations=7, auto_approve=True, prompt="hi",
    )


def test_no_repl_flag_removed():
    """-x/--no-repl was removed; -p alone now exits after the response"""
    with patch("sys.argv", ["prog", "cli", "-p", "hi", "--no-repl"]), \
         pytest.raises(SystemExit):
        from app.main import parse_args
        parse_args()


# ---------------------------------------------------------------------------
# --trace / --tracedir
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_main_trace_defaults_to_false():
    mock_store = {}
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    with patch("sys.argv", ["prog", "cli", "-p", "hi"]), \
         patch("app.cli.cli.CliAgent", return_value=agent_mock), \
         patch("app.core.runtime._store", mock_store):
        await main()
    assert mock_store.get("trace") is False


@pytest.mark.asyncio
async def test_main_trace_flag_sets_runtime_true(tmp_path):
    mock_store = {}
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    with patch("sys.argv", ["prog", "--trace", "--tracedir", str(tmp_path), "cli", "-p", "hi"]), \
         patch("app.cli.cli.CliAgent", return_value=agent_mock), \
         patch("app.core.runtime._store", mock_store):
        await main()
    assert mock_store.get("trace") is True
    assert mock_store.get("tracedir") == tmp_path


@pytest.mark.asyncio
async def test_main_tracedir_defaults_to_project_home_trace():
    mock_store = {}
    agent_mock = MagicMock()
    agent_mock.agent_loop = AsyncMock()
    with patch("sys.argv", ["prog", "cli", "-p", "hi"]), \
         patch("app.cli.cli.CliAgent", return_value=agent_mock), \
         patch("app.core.runtime._store", mock_store):
        await main()
    assert "trace" in str(mock_store.get("tracedir"))


# ---------------------------------------------------------------------------
# initialize_mcp
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initialize_mcp_no_op_when_file_missing(tmp_path):
    mock_mgr = MagicMock()
    mock_mgr.initialize = AsyncMock()
    with patch("app.core.mcp_manager.config") as mock_cfg, \
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
    with patch("app.core.mcp_manager.config") as mock_cfg, \
         patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        mock_cfg.PROJECT_HOME = str(tmp_path)
        await initialize_mcp()
    mock_mgr.initialize.assert_called_once_with(servers)


@pytest.mark.asyncio
async def test_initialize_mcp_handles_malformed_json(tmp_path):
    (tmp_path / "mcp_servers.json").write_text("not json{{")
    mock_mgr = MagicMock()
    mock_mgr.initialize = AsyncMock()
    with patch("app.core.mcp_manager.config") as mock_cfg, \
         patch("app.core.mcp_manager.mcp_manager", mock_mgr):
        mock_cfg.PROJECT_HOME = str(tmp_path)
        await initialize_mcp()  # must not raise
    mock_mgr.initialize.assert_not_called()