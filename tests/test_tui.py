"""Tests for the interactive terminal UI (app/cli/tui.py)."""

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from rich.console import Console
from rich.text import Text

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
    """Everything shown since the last call."""
    text = t.transcript.text()
    t.transcript.clear()
    return text


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

    async def read(_name=""):
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
async def test_handle_input_dispatches(tui):
    tui.run_turn = AsyncMock()
    assert await tui.handle_input("   ") is True
    assert await tui.handle_input("/raw") is True
    assert await tui.handle_input("//slash text") is True
    assert await tui.handle_input("hello") is True
    assert await tui.handle_input("/exit") is False
    assert [c.args[0] for c in tui.run_turn.await_args_list] == [
        "/slash text",
        "hello",
    ]


def test_console_logging_routed_to_tui(tui, tmp_path):
    import logging

    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        root.handlers = [
            logging.StreamHandler(),
            logging.FileHandler(tmp_path / "x.log"),
        ]
        tui_mod._route_console_logging(tui)
        kinds = [type(h) for h in root.handlers]
        assert logging.FileHandler in kinds
        assert tui_mod._ConsoleLogHandler in kinds
        assert logging.StreamHandler not in kinds
        logging.getLogger("x").warning("careful \x1b]0;title\x07")
        out = _out(tui)
        assert "warning: careful" in out and "\x1b" not in out
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

    out = "".join(t.transcript.raw)
    assert "\x1b[" in out  # rendered with styles, so escapes are really checked
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


@pytest.mark.asyncio
async def test_permission_prompt_ignores_keys_typed_ahead(tui):
    """Keys pressed right as the prompt appears don't answer it."""
    task = asyncio.ensure_future(tui._read_permission_key("bash"))
    await asyncio.sleep(0)
    assert tui.permission_pending
    tui.answer_permission("yes")  # already being typed: ignored
    await asyncio.sleep(0)
    assert not task.done()
    await asyncio.sleep(tui_mod._PERMISSION_GRACE_SECS + 0.05)
    tui.answer_permission("no")
    assert await asyncio.wait_for(task, 2) == "no"
    assert not tui.permission_pending


@pytest.mark.asyncio
async def test_ctrl_c_interrupt_is_not_delayed_by_grace(tui):
    task = asyncio.ensure_future(tui._read_permission_key("bash"))
    await asyncio.sleep(0)
    tui.on_ctrl_c()
    assert await asyncio.wait_for(task, 2) == "interrupt"


# --- full-screen layout ------------------------------------------------------


def test_transcript_rewraps_on_width_change():
    from app.cli.tui import Transcript

    tr = Transcript(Console(file=io.StringIO(), width=40))
    tr.add(Text("word " * 20))
    narrow = len(tr.lines(40))
    wide = len(tr.lines(200))
    assert narrow > wide == 1


def test_output_pane_is_bottom_anchored_and_scrolls(tui):
    from app.cli.tui import _OutputControl

    for i in range(50):
        tui.print(Text(f"line {i}"))
    control = _OutputControl(tui)
    content = control.create_content(width=100, height=10)
    assert control.vertical_scroll(None) == content.line_count - 10  # at the end
    tui.scroll(5)
    control.create_content(width=100, height=10)
    assert control.vertical_scroll(None) == content.line_count - 15
    tui.scroll(10_000)  # clamped to the top
    control.create_content(width=100, height=10)
    assert control.vertical_scroll(None) == 0


def test_on_ctrl_c_clears_input_then_arms_exit(tui):
    tui.input.text = "draft"
    tui.on_ctrl_c()
    assert tui.input.text == ""
    with patch.object(tui, "quit") as quit_:
        tui.on_ctrl_c()
        quit_.assert_not_called()  # first press only arms it
        tui.on_ctrl_c()
        quit_.assert_called_once()


@pytest.mark.asyncio
async def test_submit_queues_while_busy(tui):
    tui._turn_task = asyncio.get_running_loop().create_future()  # pretend busy
    tui.input.text = "next one"
    tui.submit()
    assert tui.input.text == ""
    assert tui._inbox.get_nowait() == "next one"
    assert "Queued" in tui._toast
    tui._turn_task.cancel()


@pytest.mark.asyncio
async def test_switching_conversation_shows_it_from_the_top(tui):
    tui.print(Text("old stuff"))

    async def fake_load(_args):
        tui.agent.conversation_id = 2
        tui.agent.messages = [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ]
        return "Loaded conversation [2] other"

    from app.channels.commands import BotCommand

    tui.registry.register(BotCommand("load", "x", fake_load))
    await tui.run_command("/load 2")
    out = _out(tui)
    assert "old stuff" not in out
    assert "earlier question" in out and "earlier answer" in out
    assert out.index("earlier answer") < out.index("Loaded conversation")


@pytest.mark.asyncio
async def test_full_screen_app_runs_a_turn_and_quits(tui):
    """Drive the real app with a pipe input: type a message, then Ctrl+D."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    tui.agent.agent_loop = AsyncMock(return_value="ok")
    with create_pipe_input() as pipe:
        runner = asyncio.ensure_future(tui.run(input=pipe, output=DummyOutput()))
        await asyncio.sleep(0.2)
        pipe.send_text("hello there\r")
        for _ in range(50):
            if tui.agent.agent_loop.await_count:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)
        pipe.send_text("\x04")  # Ctrl+D on an empty input
        await asyncio.wait_for(runner, 3)
    tui.agent.agent_loop.assert_awaited_once_with("hello there", None)
    assert "❯ hello there" in _out(tui)


def test_toolbar_drops_hints_that_do_not_fit(tui):
    from prompt_toolkit.formatted_text import to_formatted_text
    from prompt_toolkit.formatted_text.utils import fragment_list_width

    from app.core import runtime

    # Fix the status parts' width, so the result doesn't depend on test order.
    with patch.dict(
        runtime._store,
        {"model": "deepseek/deepseek-v4.1-flash", "conversation_name": "x"},
    ):
        _check_toolbar_widths(tui, to_formatted_text, fragment_list_width)


def _check_toolbar_widths(tui, to_formatted_text, fragment_list_width):
    def bar(columns):
        with patch.object(tui, "_columns", return_value=columns):
            return to_formatted_text(tui._toolbar())

    def text(frags):
        return "".join(t for _, t, *_ in frags)

    assert "scroll" in text(bar(200))  # every hint fits
    for columns in (60, 70, 90, 100, 120):
        # Hints are dropped, never cut off: the bar fits the terminal...
        assert fragment_list_width(bar(columns)) <= columns
    assert "scroll" not in text(bar(70))  # ...so a narrow one shows fewer
    assert "cmds" not in text(bar(40))  # and none when there's no room


@pytest.mark.asyncio
async def test_busy_line_cleans_the_tool_name(tui):
    from prompt_toolkit.formatted_text import to_formatted_text

    tui._turn_task = asyncio.get_running_loop().create_future()  # busy
    task = asyncio.ensure_future(tui._read_permission_key("evil\x1b]0;x\x07"))
    await asyncio.sleep(0)
    text = "".join(t for _, t, *_ in to_formatted_text(tui._busy_line()))
    assert "allow evil]0;x?" in text and "\x1b" not in text
    tui.on_ctrl_c()
    await task
    tui._turn_task.cancel()


def test_escape_does_not_answer_a_permission_prompt(tui):
    """Alt+Enter starts with Escape; it must not deny a pending tool call."""
    from prompt_toolkit.keys import Keys

    kb = tui._key_bindings()
    assert not [b for b in kb.bindings if b.keys == (Keys.Escape,)]


@pytest.mark.asyncio
async def test_real_ctrl_c_key_interrupts_at_a_permission_prompt(tui):
    """Send an actual Ctrl+C byte through the running app while a prompt is
    shown: the <any> catch-all must not swallow it."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    answers = []

    async def turn(*_):
        answers.append(await tui.ask_permission("bash", {"command": "ls"}))
        await asyncio.sleep(10)

    tui.agent.agent_loop = turn
    with create_pipe_input() as pipe:
        runner = asyncio.ensure_future(tui.run(input=pipe, output=DummyOutput()))
        await asyncio.sleep(0.2)
        pipe.send_text("go\r")
        for _ in range(50):
            if tui.permission_pending:
                break
            await asyncio.sleep(0.05)
        assert tui.permission_pending
        pipe.send_text("y")  # within the grace period: ignored
        await asyncio.sleep(0.05)
        assert tui.permission_pending
        pipe.send_text("\x03")  # Ctrl+C
        for _ in range(50):
            if not tui.busy:
                break
            await asyncio.sleep(0.05)
        assert answers == [False]  # interrupted, not approved
        assert not tui.busy  # and the turn was cancelled
        pipe.send_text("\x04")
        await asyncio.wait_for(runner, 3)
    assert "Interrupted" in _out(tui)


def test_shift_tab_is_ignored_while_a_permission_prompt_is_pending(tui):
    from prompt_toolkit.keys import Keys

    kb = tui._key_bindings()
    (binding,) = [b for b in kb.bindings if b.keys == (Keys.BackTab,)]
    tui._perm_future = MagicMock(done=lambda: False)  # a pending prompt
    try:
        assert not binding.filter()  # pending prompt: Shift+Tab does nothing
        tui._perm_future = None
        assert binding.filter()
    finally:
        tui._perm_future = None
