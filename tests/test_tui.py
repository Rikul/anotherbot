"""Tests for the interactive terminal UI (app/cli/tui.py)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from rich.console import Console

from app.cli import tui as tui_mod
from app.cli.cli_agent import INTERRUPTED_REPLY, CliAgent
from app.cli.tui import (
    Tui,
    TuiCompleter,
    extract_attachments,
    format_tokens,
    osc52_copy,
    summarize_args,
    summarize_result,
)


# --- helpers -----------------------------------------------------------------


def test_summarize_args_single_value_and_truncation():
    assert summarize_args({}, 50) == ""
    assert summarize_args({"command": "ls   -la\n  /tmp"}, 50) == "ls -la /tmp"
    out = summarize_args({"a": "x" * 100, "b": 2}, 30)
    assert len(out) == 30 and out.endswith("…")


def test_summarize_result():
    assert summarize_result("") == "(no output)"
    assert summarize_result("\n first \nsecond\nthird") == "first  (+2 lines)"
    assert summarize_result("only") == "only"


def test_format_tokens():
    assert format_tokens(999) == "999"
    assert format_tokens(1234) == "1.2k"
    assert format_tokens(1_500_000) == "1.5M"


def test_extract_attachments(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.txt").write_text("y")
    text = "look at @a.png and @b.txt, also @missing.txt and me@a.png @a.png"
    found = extract_attachments(text, cwd=tmp_path)
    assert found == [str(tmp_path / "a.png"), str(tmp_path / "b.txt")]


def test_osc52_copy():
    seq = osc52_copy("hi")
    assert seq == "\033]52;c;aGk=\a"


# --- completer ---------------------------------------------------------------


def _complete(completer, text):
    doc = Document(text, len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def _completer():
    return TuiCompleter(
        lambda: [("help", "Show help"), ("history", "Recent"), ("load", "Load")],
        lambda: [
            {"id": 12, "name": "trip", "channel": "cli", "message_count": 4},
            {"id": 3, "name": "old", "channel": "web", "message_count": 1},
        ],
    )


def test_completer_slash_commands():
    names = [c.text for c in _complete(_completer(), "/h")]
    assert names == ["/help", "/history"]


def test_completer_conversation_ids():
    comps = _complete(_completer(), "/load 1")
    assert [c.text for c in comps] == ["12"]
    assert _complete(_completer(), "/help 1") == []


def test_completer_paths(tmp_path):
    (tmp_path / "notes.md").write_text("x")
    comps = _complete(_completer(), f"see @{tmp_path}/no")
    assert [c.text for c in comps] == ["tes.md"]


def test_completer_plain_text_has_no_completions():
    assert _complete(_completer(), "hello wor") == []


# --- Tui ---------------------------------------------------------------------


@pytest.fixture
def tui(tmp_path):
    """A Tui with a mocked agent store and a recording console."""
    console = Console(record=True, width=100, force_terminal=False)
    with (
        patch("app.core.agent.Client"),
        patch("app.cli.cli_agent.ConversationStore") as store_cls,
        patch("app.cli.cli_agent.MessageHistory"),
    ):
        store = store_cls.return_value
        store.get_last.return_value = {"id": 1, "name": "New Conversation"}
        store.load_messages.return_value = []
        store.get.return_value = {"id": 1, "name": "New Conversation"}
        t = Tui(console=console, history_file=None)
    return t


def _out(t):
    return t.console.export_text(clear=True)


def test_tui_agent_uses_ui(tui):
    assert isinstance(tui.agent, CliAgent)
    assert tui.agent.ui is tui


@pytest.mark.asyncio
async def test_permission_always_remembers_tool(tui):
    with patch.object(tui, "_read_permission_key", AsyncMock(return_value="always")):
        assert await tui.ask_permission("bash", {"command": "ls"}) is True
    # remembered: no second prompt
    with patch.object(tui, "_read_permission_key", AsyncMock()) as read:
        assert await tui.ask_permission("bash", {"command": "pwd"}) is True
        read.assert_not_called()
    assert "Allow bash?" in _out(tui)


@pytest.mark.asyncio
async def test_permission_no_and_all(tui):
    with patch.object(tui, "_read_permission_key", AsyncMock(return_value="no")):
        assert await tui.ask_permission("bash", {"command": "rm -rf x"}) is False
    with patch.object(tui, "_read_permission_key", AsyncMock(return_value="all")):
        assert await tui.ask_permission("write_file", {}) is True
    assert tui.agent.auto_approve is True


@pytest.mark.asyncio
async def test_parallel_permission_prompts_are_serialised(tui):
    active = 0
    peak = 0

    async def read():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "yes"

    with patch.object(tui, "_read_permission_key", side_effect=read):
        results = await asyncio.gather(
            *(tui.ask_permission("bash", {"n": i}) for i in range(3))
        )
    assert results == [True, True, True]
    assert peak == 1


def test_toggle_auto_approve(tui):
    assert tui.toggle_auto_approve() is True
    assert tui.agent.auto_approve is True
    assert tui.toggle_auto_approve() is False


@pytest.mark.asyncio
async def test_run_command_local_and_shared(tui):
    assert await tui.run_command("/raw") is True
    assert tui.render_markdown is False
    assert await tui.run_command("/nosuch") is True
    assert "Unknown command: /nosuch" in _out(tui)
    assert await tui.run_command("/help") is True
    out = _out(tui)
    assert "/status" in out and "/yolo" in out
    assert await tui.run_command("/exit") is False


@pytest.mark.asyncio
async def test_run_turn_passes_attachments_and_counts_tokens(tui, tmp_path):
    (tmp_path / "pic.png").write_bytes(b"x")
    tui.agent._llm_calls = [
        {"usage": {"prompt_tokens": 100, "completion_tokens": 7}},
        {"usage": {"prompt_tokens": 200, "completion_tokens": 3}},
    ]
    tui.agent.agent_loop = AsyncMock(return_value="ok")
    await tui.run_turn(f"what is @{tmp_path}/pic.png")
    tui.agent.agent_loop.assert_awaited_once_with(
        f"what is @{tmp_path}/pic.png", {"files": [str(tmp_path / "pic.png")]}
    )
    assert tui.session_tokens == {"in": 300, "out": 10}
    assert "300 in / 10 out" in _out(tui)


@pytest.mark.asyncio
async def test_run_turn_reports_errors_and_keeps_going(tui):
    tui.agent.agent_loop = AsyncMock(side_effect=RuntimeError("boom"))
    await tui.run_turn("hi")
    assert "RuntimeError: boom" in _out(tui)


@pytest.mark.asyncio
async def test_run_turn_cancelled_shows_interrupted(tui):
    async def slow(*_):
        await asyncio.sleep(10)

    tui.agent.agent_loop = slow
    turn = asyncio.ensure_future(tui.run_turn("hi"))
    await asyncio.sleep(0.05)
    # what the SIGINT handler does: cancel the agent task, not the TUI
    inner = [t for t in asyncio.all_tasks() if t is not turn and "slow" in repr(t)]
    assert inner
    inner[0].cancel()
    await turn
    assert "Interrupted" in _out(tui)


def test_hooks_print(tui):
    tui.thinking("planning")
    tui.tool_start("bash", {"command": "ls"})
    tui.tool_result("bash", "a\nb")
    tui.tool_result("bash", "Error running tool bash: nope")
    tui.response("**done**")
    out = _out(tui)
    assert "planning" in out
    assert "⏺ bash  ls" in out
    assert "⎿ a  (+1 lines)" in out
    assert "nope" in out
    assert "done" in out and "**" not in out  # rendered as markdown
    assert tui.last_reply == "**done**"


def test_show_history(tui):
    tui.agent.messages = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    tui.show_history(2)
    out = _out(tui)
    assert "q1" not in out and "a1" in out and "q2" in out


@pytest.mark.asyncio
async def test_run_loop_exits_on_eof_and_dispatches(tui):
    inputs = iter(["", "/raw", "//slash text", "hello"])

    async def read_input():
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError from None

    tui.read_input = read_input
    tui.run_turn = AsyncMock()
    await tui.run()
    assert [c.args[0] for c in tui.run_turn.await_args_list] == [
        "/slash text",
        "hello",
    ]
    assert "Bye." in _out(tui)


def test_quiet_console_logging_keeps_file_handler(tmp_path):
    import logging

    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        root.handlers = [
            logging.StreamHandler(),
            logging.FileHandler(tmp_path / "x.log"),
        ]
        console = Console(record=True)
        tui_mod._quiet_console_logging(console)
        kinds = [type(h) for h in root.handlers]
        assert logging.FileHandler in kinds
        assert tui_mod._ConsoleLogHandler in kinds
        assert logging.StreamHandler not in kinds
    finally:
        for h in root.handlers:
            if h not in saved:
                h.close()
        root.handlers = saved


# --- CliAgent interruption ---------------------------------------------------


@pytest.mark.asyncio
async def test_cli_agent_records_interrupted_turn():
    with (
        patch("app.core.agent.Client"),
        patch("app.cli.cli_agent.ConversationStore") as store_cls,
        patch("app.cli.cli_agent.MessageHistory") as hist_cls,
        patch("app.cli.cli_agent.get_default_sys_prompt", return_value=""),
    ):
        store_cls.return_value.get_last.return_value = {"id": 5, "name": "x"}
        store_cls.return_value.load_messages.return_value = []
        agent = CliAgent(ui=MagicMock())

        async def hang(*_):
            await asyncio.sleep(10)

        agent._loop = hang
        task = asyncio.ensure_future(agent.agent_loop("hi"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert agent.messages[-2:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": INTERRUPTED_REPLY},
    ]
    hist_cls.return_value.add_message.assert_any_call("assistant", INTERRUPTED_REPLY, 5)


@pytest.mark.asyncio
async def test_cli_agent_ui_hooks():
    ui = MagicMock()
    ui.ask_permission = AsyncMock(return_value=False)
    with (
        patch("app.core.agent.Client"),
        patch("app.cli.cli_agent.ConversationStore") as store_cls,
        patch("app.cli.cli_agent.MessageHistory"),
    ):
        store_cls.return_value.get_last.return_value = {"id": 1, "name": "x"}
        store_cls.return_value.load_messages.return_value = []
        agent = CliAgent(ui=ui)

    call = SimpleNamespace(
        function=SimpleNamespace(name="bash", arguments=json.dumps({"command": "ls"}))
    )
    result = await agent.handle_tool_call(call)
    assert result.startswith("User denied permission")
    ui.ask_permission.assert_awaited_once_with("bash", {"command": "ls"})
    ui.tool_result.assert_called_once_with("bash", result)
    await agent._on_thinking("  hmm  ")
    ui.thinking.assert_called_once_with("hmm")
    await agent._on_response("final")
    ui.response.assert_called_once_with("final")


# --- terminal escape sequences from model/tool text ---------------------------

OSC52 = "\x1b]52;c;aGk=\x07"


def test_clean_strips_control_characters():
    from app.cli.tui import clean

    assert clean(f"a{OSC52}b\tc\nd\x9be") == "a]52;c;aGk=b\tc\nde"


def test_untrusted_text_never_reaches_terminal_with_escapes():
    console = Console(record=True, width=200, force_terminal=True)
    with (
        patch("app.core.agent.Client"),
        patch("app.cli.cli_agent.ConversationStore") as store_cls,
        patch("app.cli.cli_agent.MessageHistory"),
    ):
        store_cls.return_value.get_last.return_value = {"id": 1, "name": "x"}
        store_cls.return_value.load_messages.return_value = []
        t = Tui(console=console, history_file=None)

    t.thinking(f"think {OSC52}")
    t.tool_start("bash", {"command": f"cat {OSC52}"})
    t.tool_result("bash", f"out {OSC52}")
    t.response(f"reply {OSC52}")
    t.render_markdown = False
    t.response(f"raw {OSC52}")
    t._print_permission_request("bash", {"a": f"x{OSC52}", "b": f"1\n2{OSC52}"})
    t.agent.messages = [{"role": "user", "content": f"q {OSC52}"}]
    t.show_history(1)

    out = console.export_text(styles=True)
    # rich's own styling uses CSI (ESC [); no OSC (ESC ]) or BEL may get through
    assert "\x1b]" not in out
    assert "\x07" not in out


@pytest.mark.asyncio
async def test_ctrl_c_at_permission_prompt_interrupts_the_turn(tui):
    """Ctrl+C in the (raw-mode) permission prompt cancels the whole turn."""
    ran = []

    async def turn(*_):
        allowed = await tui.ask_permission("bash", {"command": "ls"})
        ran.append(allowed)
        # what CliAgent.handle_tool_call does next; not shown while interrupting
        tui.tool_result("bash", "User denied permission to run this tool.")
        await asyncio.sleep(10)

    tui.agent.agent_loop = turn
    with patch.object(tui, "_read_permission_key", AsyncMock(return_value="interrupt")):
        await asyncio.wait_for(tui.run_turn("hi"), timeout=2)
    # ask_permission returns False and the cancel lands at the next await
    assert ran == [False]
    out = _out(tui)
    assert "Interrupted" in out
    assert "User denied" not in out


# --- review fixes: malformed @tokens, typed-ahead keys -------------------------


def test_extract_attachments_skips_unresolvable_tokens(tmp_path):
    (tmp_path / "ok.txt").write_text("x")
    text = "ping @~nosuchuser-xyz and @ok.txt and @\x00bad"
    assert extract_attachments(text, cwd=tmp_path) == [str(tmp_path / "ok.txt")]


def test_discard_typeahead_flushes_a_tty():
    with (
        patch("sys.stdin") as stdin,
        patch("termios.tcflush") as flush,
    ):
        stdin.isatty.return_value = True
        stdin.fileno.return_value = 7
        tui_mod.discard_typeahead()
    flush.assert_called_once()
    assert flush.call_args.args[0] == 7


def test_discard_typeahead_ignores_non_tty():
    with patch("sys.stdin") as stdin, patch("termios.tcflush") as flush:
        stdin.isatty.return_value = False
        tui_mod.discard_typeahead()
    flush.assert_not_called()


@pytest.mark.asyncio
async def test_permission_prompt_ignores_keys_typed_ahead(tui):
    """Keys already waiting when the prompt opens don't answer it."""
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=DummyOutput()),
    ):
        pipe.send_text("y")  # typed during the turn
        task = asyncio.ensure_future(tui._read_permission_key())
        await asyncio.sleep(tui_mod._PERMISSION_GRACE_SECS + 0.2)
        assert not task.done()  # the early "y" was ignored
        pipe.send_text("n")
        assert await asyncio.wait_for(task, 2) == "no"
