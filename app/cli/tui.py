"""Full-screen terminal UI for the CLI agent (prompt_toolkit layout, rich rendering).

Layout, top to bottom: the conversation (scrollable), a status line while a
turn runs, the input box between two rules (pinned to the bottom), and a
status bar. Output is rendered by rich into ANSI text and shown by
prompt_toolkit, so it re-wraps when the terminal is resized.

Features:

- line editing, persistent history (Up/Down, Ctrl+R search), multi-line
  input (Alt+Enter / Ctrl+J, or end a line with ``\\``), pasting;
- you can type while a turn runs; Enter queues the message for after it;
- completion for slash commands, conversation ids after ``/load`` & co., and
  ``@path`` file attachments;
- markdown-rendered replies (``/raw`` toggles), a spinner with elapsed time
  and the running tool, compact tool call/result lines, per-turn timing and
  token counts;
- single-key tool permission prompts (yes / no / always for this tool /
  approve everything), Shift+Tab toggles auto-approve;
- Ctrl+C interrupts a running turn; otherwise it clears the input, and twice
  in a row (or Ctrl+D) quits. PageUp/PageDown and the mouse wheel scroll.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
import time
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML, to_formatted_text
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
    UIContent,
    UIControl,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import SearchToolbar
from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule
from rich.text import Text

from .. import config
from ..channels.commands import CommandRegistry, build_command_registry
from ..core import runtime
from ..infra.app_logging import log
from .cli_agent import CliAgent
from .tui_text import (  # noqa: F401  # re-exported for callers and tests
    Transcript,
    TuiCompleter,
    _h,
    clean,
    extract_attachments,
    format_tokens,
    one_line,
    osc52_copy,
    summarize_args,
    summarize_result,
)

HISTORY_FILE = config.PROJECT_HOME / "cli_history"

# Commands the TUI handles itself (the rest go to the shared CommandRegistry).
LOCAL_COMMANDS = {
    "clear": "Clear the screen (also Ctrl+L).",
    "history": "Show recent messages of this conversation. Usage: /history [n]",
    "copy": "Copy the last reply to the clipboard.",
    "raw": "Toggle markdown rendering of replies.",
    "yolo": "Toggle auto-approve for tool calls (also Shift+Tab).",
    "exit": "Quit.",
    "quit": "Quit.",
}

_EXIT_CONFIRM_SECS = 2.0
# Keys arriving this soon after a permission prompt appears are ignored: they
# were typed before the user could have read it.
_PERMISSION_GRACE_SECS = 0.3
# Messages of a conversation shown when it is opened (/history shows more).
_HISTORY_ON_OPEN = 200
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Status bar key hints, shown while they fit.
_HINTS = (
    ("/", "cmds"),
    ("@", "attach"),
    ("⇧Tab", "approve"),
    ("Alt+⏎", "newline"),
    ("PgUp", "scroll"),
)

STYLE = Style.from_dict(
    {
        "prompt": "ansicyan bold",
        "continuation": "ansibrightblack",
        "rule": "ansibrightblack",
        "busy": "ansicyan",
        "busy.dim": "ansibrightblack",
        "toolbar": "ansibrightblack",
        "toolbar.key": "ansicyan",
        "toolbar.warn": "ansiyellow bold",
        "toolbar.ok": "ansigreen",
        "perm": "ansiyellow bold",
        "perm.key": "ansicyan bold",
    }
)


# --- output pane -------------------------------------------------------------


class _OutputControl(UIControl):
    """Shows the transcript, bottom-anchored, scrolled by ``Tui.scroll_up``."""

    def __init__(self, tui: Tui) -> None:
        self.tui = tui
        self.bottom = 0  # index of the last visible line
        self.height = 0

    def create_content(self, width: int, height: int) -> UIContent:
        lines = self.tui.transcript.lines(width)
        count = len(lines)
        max_up = max(0, count - height)
        self.tui.scroll_up = min(self.tui.scroll_up, max_up)
        self.bottom = max(0, count - 1 - self.tui.scroll_up)
        self.height = height
        return UIContent(
            get_line=lambda i: lines[i],
            line_count=count,
            cursor_position=Point(0, self.bottom),
            show_cursor=False,
        )

    def vertical_scroll(self, _window) -> int:
        """Top line, so that ``bottom`` is the window's last row."""
        return max(0, self.bottom - self.height + 1)

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.tui.scroll(3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.tui.scroll(-3)
            return None
        return NotImplemented

    def is_focusable(self) -> bool:
        return False


class _ConsoleLogHandler(logging.Handler):
    """Shows warnings and errors in the transcript (from any thread)."""

    def __init__(self, tui: Tui) -> None:
        super().__init__(logging.WARNING)
        self.tui = tui

    def emit(self, record: logging.LogRecord) -> None:
        try:
            style = "red" if record.levelno >= logging.ERROR else "yellow"
            line = Text(
                f"{record.levelname.lower()}: {clean(record.getMessage())}",
                style=style,
            )
            self.tui.print_threadsafe(line)
        except Exception:  # pylint: disable=broad-exception-caught  # logging must never raise
            self.handleError(record)


# --- the TUI -----------------------------------------------------------------


class Tui:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """The interactive session: shows the conversation, reads input, runs turns.

    Args:
        max_iterations: passed to the ``CliAgent``.
        auto_approve: start with tool calls auto-approved.
        console: rich console used to render output (tests pass their own).
        history_file: where input history is kept; ``None`` keeps it in memory.
    """

    def __init__(
        self,
        max_iterations: int = 250,
        auto_approve: bool = False,
        console: Console | None = None,
        history_file: Path | None = HISTORY_FILE,
    ) -> None:
        self.console = console or Console(
            file=io.StringIO(),
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            width=100,
        )
        self.transcript = Transcript(self.console)
        self.agent = CliAgent(
            max_iterations=max_iterations, auto_approve=auto_approve, ui=self
        )
        self.registry: CommandRegistry = build_command_registry(self.agent)
        self.render_markdown = True
        self.last_reply = ""
        self.allowed_tools: set[str] = set()
        self.session_tokens = {"in": 0, "out": 0}
        self.scroll_up = 0  # lines scrolled up from the bottom of the output
        self.app: Application | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self._activity = ""  # what the busy line says we're doing
        self._turn_started = 0.0
        self._perm_lock = asyncio.Lock()
        self._perm_future: asyncio.Future | None = None  # pending permission answer
        self._perm_shown_at = 0.0
        self._perm_name = ""
        self._turn_task: asyncio.Future | None = None  # the running agent turn
        self._interrupting = False  # Ctrl+C at a permission prompt, turn ending
        self._exit_armed_at = 0.0
        self._toast = ""  # short message shown in the status bar…
        self._toast_until = 0.0  # …until this time.monotonic()
        self._search = SearchToolbar()
        self._input_window: Window | None = None  # set by build_app()
        self.input = Buffer(
            multiline=True,
            completer=TuiCompleter(self._command_list, self._conversation_list),
            complete_while_typing=True,
            history=self._history(history_file),
            read_only=Condition(lambda: self.permission_pending),
        )

    @staticmethod
    def _history(path: Path | None):
        if path is None:
            return InMemoryHistory()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return FileHistory(str(path))
        except OSError as e:
            log.warning("Can't use history file %s: %s", path, e)
            return InMemoryHistory()

    # --- state -----------------------------------------------------------------

    @property
    def busy(self) -> bool:
        """Whether an agent turn is running."""
        return self._turn_task is not None

    @property
    def permission_pending(self) -> bool:
        """Whether a tool permission prompt is waiting for an answer."""
        return self._perm_future is not None and not self._perm_future.done()

    # --- output ----------------------------------------------------------------

    def print(self, *renderables) -> None:
        """Append to the conversation pane (an empty call adds a blank line)."""
        for renderable in renderables or (Text(""),):
            self.transcript.add(renderable)
        self.invalidate()

    def print_threadsafe(self, renderable) -> None:
        """``print`` from any thread (log records can come from tool threads)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            self.print(renderable)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self.print(renderable)
        else:
            loop.call_soon_threadsafe(self.print, renderable)

    def invalidate(self) -> None:
        """Ask for a redraw (no-op before the app runs)."""
        if self.app is not None and self.app.is_running:
            self.app.invalidate()

    def scroll(self, lines: int) -> None:
        """Scroll the conversation up (positive) or down (negative)."""
        self.scroll_up = max(0, self.scroll_up + lines)
        self.invalidate()

    # --- completion sources --------------------------------------------------

    def _command_list(self) -> list[tuple[str, str]]:
        cmds = {c.name: c.description for c in self.registry.list()}
        cmds.update(LOCAL_COMMANDS)
        return sorted(cmds.items())

    def _conversation_list(self) -> list[dict]:
        return self.agent.store.list()

    # --- layout ----------------------------------------------------------------

    def build_app(self, **app_kwargs) -> Application:
        """The full-screen application (``app_kwargs`` go to ``Application``)."""
        output = _OutputControl(self)
        perm = Condition(lambda: self.permission_pending)
        busy = Condition(lambda: self.busy)
        self._input_window = Window(
            BufferControl(
                self.input,
                search_buffer_control=self._search.control,
                preview_search=True,
            ),
            height=Dimension(min=1, max=12),
            wrap_lines=True,
            dont_extend_height=True,
            get_line_prefix=self._line_prefix,
        )

        def rule():
            return Window(height=1, char="─", style="class:rule")

        body = HSplit(
            [
                Window(
                    output,
                    wrap_lines=False,
                    get_vertical_scroll=output.vertical_scroll,
                    always_hide_cursor=True,
                ),
                ConditionalContainer(
                    Window(FormattedTextControl(self._busy_line), height=1),
                    filter=busy,
                ),
                rule(),
                ConditionalContainer(
                    Window(
                        FormattedTextControl(self._permission_bar),
                        height=1,
                        style="class:perm",
                    ),
                    filter=perm,
                ),
                ConditionalContainer(self._input_window, filter=~perm),
                self._search,
                rule(),
                Window(
                    FormattedTextControl(self._toolbar),
                    height=1,
                    style="class:toolbar",
                ),
            ]
        )
        root = FloatContainer(
            body,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=8, scroll_offset=1),
                )
            ],
        )
        self.app = Application(
            layout=Layout(root, focused_element=self._input_window),
            key_bindings=self._key_bindings(),
            style=STYLE,
            full_screen=True,
            mouse_support=True,
            **app_kwargs,
        )
        return self.app

    @staticmethod
    def _line_prefix(line: int, wrap_count: int):
        if line == 0 and wrap_count == 0:
            return HTML("<prompt>❯ </prompt>")
        return HTML("<continuation>  </continuation>")

    def _busy_line(self):
        if not self.busy:
            return ""
        elapsed = int(time.monotonic() - self._turn_started)
        frame = _SPINNER[int(time.monotonic() * 10) % len(_SPINNER)]
        activity = (
            f"waiting for you: allow {clean(self._perm_name)}?"
            if self.permission_pending
            else self._activity
        )
        queued = self._inbox.qsize()
        extra = f" · {queued} queued" if queued else ""
        return HTML(
            f"<busy>{frame} {_h(activity)}</busy>"
            f"<busy.dim> {elapsed}s{extra} · Ctrl+C to interrupt</busy.dim>"
        )

    def _permission_bar(self):
        return HTML(
            f"Allow {_h(clean(self._perm_name))}?  "
            "<perm.key>y</perm.key> yes  <perm.key>n</perm.key> no  "
            "<perm.key>a</perm.key> always this tool  "
            "<perm.key>!</perm.key> approve all  "
            "<perm.key>Ctrl+C</perm.key> interrupt"
        )

    def _toolbar(self):
        model = runtime.get("model", "?")
        conv_id = runtime.get("conversation_id", "—")
        conv_name = runtime.get("conversation_name", "")
        approve = (
            "<toolbar.warn>auto-approve</toolbar.warn>"
            if self.agent.auto_approve
            else "<toolbar.ok>ask before tools</toolbar.ok>"
        )
        toks = self.session_tokens["in"] + self.session_tokens["out"]
        parts = [
            f" {_h(clean(model))}",
            f"#{conv_id} {_h(one_line(conv_name, 40))}",
            approve,
        ]
        if toks:
            parts.append(f"{format_tokens(toks)} tok")
        if not self.render_markdown:
            parts.append("raw")
        if self.scroll_up:
            parts.append(f"<toolbar.warn>↑ {self.scroll_up} lines</toolbar.warn>")
        left = "  │  ".join(parts)
        if self._toast and time.monotonic() < self._toast_until:
            return HTML(f"{left}  │  <toolbar.warn>{_h(self._toast)}</toolbar.warn>")
        # Key hints, as many as fit after the status parts.
        room = self._columns() - fragment_list_width(to_formatted_text(HTML(left))) - 5
        shown = []
        for key, label in _HINTS:
            item = f"{key} {label}"
            if len("  ".join([*shown, item])) > room:
                break
            shown.append(item)
        if not shown:
            return HTML(left)
        hints = "  ".join(
            f"<toolbar.key>{_h(k)}</toolbar.key> {_h(v)}"
            for k, v in _HINTS[: len(shown)]
        )
        return HTML(f"{left}  │  {hints}")

    def _columns(self) -> int:
        try:
            return self.app.output.get_size().columns if self.app else 100
        except Exception:  # pylint: disable=broad-exception-caught  # e.g. no terminal
            return 100

    # --- keys ------------------------------------------------------------------

    def _key_bindings(self) -> KeyBindings:  # pylint: disable=too-many-locals
        kb = KeyBindings()
        perm = Condition(lambda: self.permission_pending)
        empty = Condition(lambda: not self.input.text)

        @kb.add("enter", filter=~perm)
        def _enter(event):
            buf = event.current_buffer
            state = buf.complete_state
            if state and state.current_completion:
                # Accept the highlighted completion instead of sending.
                buf.apply_completion(state.current_completion)
                return
            if buf.document.text_before_cursor.endswith("\\"):
                buf.delete_before_cursor(1)
                buf.insert_text("\n")
                return
            self.submit()

        @kb.add("escape", "enter", filter=~perm)
        @kb.add("c-j", filter=~perm)
        def _newline(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def _ctrl_c(event):
            self.on_ctrl_c(event.app)

        @kb.add("c-d", filter=empty & ~perm)
        def _ctrl_d(event):
            self.quit(event.app)

        @kb.add("s-tab")
        def _toggle_approve(_event):
            self.toggle_auto_approve()

        @kb.add("c-l")
        def _clear(_event):
            self._cmd_clear("")

        @kb.add("pageup")
        def _page_up(_event):
            self.scroll(max(1, self._page_size()))

        @kb.add("pagedown")
        def _page_down(_event):
            self.scroll(-max(1, self._page_size()))

        answers = {
            "y": "yes", "Y": "yes", "enter": "yes",
            "n": "no", "N": "no", "escape": "no",
            "a": "always", "A": "always",
            "!": "all",
        }  # fmt: skip
        for key, value in answers.items():
            kb.add(key, filter=perm, eager=True)(
                lambda _e, v=value: self.answer_permission(v)
            )

        @kb.add("<any>", filter=perm)
        def _ignore(_event):
            pass  # only the answer keys do anything while a prompt is shown

        return kb

    def _page_size(self) -> int:
        size = self.app.output.get_size() if self.app else None
        return (size.rows - 6) if size else 20

    def on_ctrl_c(self, app: Application | None = None) -> None:
        """Interrupt the turn (or the permission prompt), clear, or quit."""
        if self.permission_pending:
            self.answer_permission("interrupt")
            return
        if self.busy:
            self._turn_task.cancel()
            return
        if self.input.text:
            self.input.reset()
            return
        now = time.monotonic()
        if now - self._exit_armed_at < _EXIT_CONFIRM_SECS:
            self.quit(app)
            return
        self._exit_armed_at = now
        self.toast("Press Ctrl+C again to quit", _EXIT_CONFIRM_SECS)

    def quit(self, app: Application | None = None) -> None:
        """Stop any running turn and leave the app."""
        if self._turn_task is not None:
            self._turn_task.cancel()
        app = app or self.app
        if app is not None and app.is_running and not app.future.done():
            app.exit()

    def submit(self) -> None:
        """Send the input box's text (queued if a turn is running)."""
        text = self.input.text.strip()
        if not text:
            return
        self.input.append_to_history()
        self.input.reset()
        self.scroll_up = 0
        if self.busy or not self._inbox.empty():
            self.toast("Queued: it runs after the current turn")
        self._inbox.put_nowait(text)
        self.invalidate()

    def toggle_auto_approve(self) -> bool:
        """Flip auto-approve; returns the new state."""
        self.agent.auto_approve = not self.agent.auto_approve
        self.toast(
            "Auto-approve ON: tools run without asking"
            if self.agent.auto_approve
            else "Auto-approve off"
        )
        return self.agent.auto_approve

    def toast(self, message: str, seconds: float = 3.0) -> None:
        """Show ``message`` in the status bar for a few seconds."""
        self._toast = message
        self._toast_until = time.monotonic() + seconds
        self.invalidate()

    # --- CliUI hooks (called by CliAgent) --------------------------------------

    def thinking(self, text: str) -> None:
        """Assistant text written alongside tool calls."""
        self.print(Text(clean(text), style="dim italic"))

    def tool_start(self, name: str, args: dict) -> None:
        """One line per tool call; also shown on the busy line."""
        width = max(20, self.console.width - len(name) - 6)
        summary = summarize_args(args, width)
        line = Text("⏺ ", style="cyan")
        line.append(clean(name), style="bold cyan")
        if summary:
            line.append(f"  {summary}", style="dim")
        self.print(line)
        self._set_activity(f"{clean(name)} {one_line(summary, 40)}".strip())

    def tool_result(self, name: str, result: str) -> None:
        """A dim one-line preview of what the tool returned."""
        if self._interrupting:
            return  # the turn is being cancelled; "denied" would be misleading
        failed = (result or "").startswith(
            (f"Error running tool {name}", "User denied permission")
        )
        self.print(
            Text("  ⎿ " + summarize_result(result), style="red" if failed else "dim")
        )
        self._set_activity("thinking")

    def response(self, text: str) -> None:
        """The final reply, as markdown unless /raw is on."""
        self.last_reply = text.strip()
        self.print()
        self.print(self._render(self.last_reply))

    async def ask_permission(self, name: str, args: dict) -> bool:
        """Single-key prompt: y/Enter yes, n/Esc no, a always this tool, ! everything.

        Ctrl+C interrupts the whole turn.
        """
        if name in self.allowed_tools or self.agent.auto_approve:
            return True
        async with self._perm_lock:  # parallel tool calls ask one at a time
            if name in self.allowed_tools or self.agent.auto_approve:
                return True
            self._print_permission_request(name, args)
            answer = await self._read_permission_key(name)
        if answer == "interrupt":
            self._interrupting = True
            if self._turn_task is not None:
                self._turn_task.cancel()
            return False
        if answer == "always":
            self.allowed_tools.add(name)
            self.print(Text(f"  ✓ {clean(name)} allowed for this session", "green"))
        elif answer == "all":
            self.agent.auto_approve = True
            self.print(Text("  ✓ auto-approve on (Shift+Tab to turn off)", "yellow"))
        elif answer == "no":
            self.print(Text("  ✗ denied", style="red"))
        return answer != "no"

    def _print_permission_request(self, name: str, args: dict) -> None:
        self.print()
        self.print(
            Text.assemble(
                ("Allow ", "yellow"), (clean(name), "bold yellow"), ("?", "yellow")
            )
        )
        for key, value in (args or {}).items():
            text = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False)
            )
            if len(text) > 1500:
                text = text[:1500] + f"… ({len(text) - 1500} more chars)"
            if "\n" in text:
                self.print(Text(f"  {clean(key)}:", style="dim"))
                self.print(Text("    " + clean(text).replace("\n", "\n    ")))
            else:
                self.print(Text.assemble((f"  {clean(key)}: ", "dim"), clean(text)))

    async def _read_permission_key(self, name: str = "") -> str:
        """Show the permission bar and wait for ``answer_permission``."""
        self._perm_future = asyncio.get_running_loop().create_future()
        self._perm_shown_at = time.monotonic()
        self._perm_name = name
        self.scroll_up = 0  # make sure the request is visible
        self.invalidate()
        try:
            return await self._perm_future
        finally:
            self._perm_future = None
            self.invalidate()

    def answer_permission(self, value: str) -> None:
        """Answer the pending permission prompt (keys within the grace period
        after it appeared are ignored, except Ctrl+C)."""
        if not self.permission_pending:
            return
        if value != "interrupt" and (
            time.monotonic() - self._perm_shown_at < _PERMISSION_GRACE_SECS
        ):
            return  # typed before the prompt could be read
        self._perm_future.set_result(value)

    # --- busy line -------------------------------------------------------------

    def _set_activity(self, activity: str) -> None:
        self._activity = activity
        self.invalidate()

    async def _tick(self) -> None:
        """Redraw for the spinner and toasts."""
        while True:
            await asyncio.sleep(0.1 if self.busy else 0.5)
            self.invalidate()

    # --- turns -----------------------------------------------------------------

    async def run_turn(self, text: str) -> None:
        """Run one agent turn; Ctrl+C cancels it."""
        self.print()
        self.print(Text("❯ " + clean(text), style="bold"))
        files = extract_attachments(text)
        metadata = {"files": files} if files else None
        for f in files:
            self.print(Text(f"  📎 {clean(Path(f).name)}", style="dim"))

        self._turn_started = time.monotonic()
        self._activity = "thinking"
        task = asyncio.ensure_future(self.agent.agent_loop(text, metadata))
        self._turn_task = task
        self._interrupting = False
        self.invalidate()
        try:
            await asyncio.wait({task})
        finally:
            self._turn_task = None
            if not task.done():
                task.cancel()

        if task.cancelled():
            self.print(Text("⏹ Interrupted", style="yellow"))
        elif task.exception() is not None:
            err = task.exception()
            log.debug("Turn failed", exc_info=err)
            self.print(Text(f"✗ {type(err).__name__}: {clean(str(err))}", style="red"))
        self._print_turn_stats()

    def _print_turn_stats(self) -> None:
        calls = self.agent._llm_calls  # pylint: disable=protected-access  # our own agent
        usage = self.agent._sum_usage(calls)  # pylint: disable=protected-access
        tok_in = usage.get("prompt_tokens", 0)
        tok_out = usage.get("completion_tokens", 0)
        self.session_tokens["in"] += tok_in
        self.session_tokens["out"] += tok_out
        elapsed = time.monotonic() - self._turn_started
        parts = [f"{elapsed:.1f}s"]
        if len(calls) > 1:
            parts.append(f"{len(calls)} steps")
        if tok_in or tok_out:
            parts.append(f"{format_tokens(tok_in)} in / {format_tokens(tok_out)} out")
        self.print(Text("  " + " · ".join(parts), style="dim"))

    # --- commands --------------------------------------------------------------

    async def handle_input(self, text: str) -> bool:
        """Run one submitted message or command. Returns False to quit."""
        text = text.strip()
        if not text:
            return True
        if text.startswith("/") and not text.startswith("//"):
            return await self.run_command(text)
        # "//" escapes a message that really starts with a slash
        await self.run_turn(text[1:] if text.startswith("//") else text)
        return True

    async def run_command(self, line: str) -> bool:
        """Run a slash command. Returns False when the session should end."""
        name, _, args = line[1:].partition(" ")
        name = name.lower()
        args = args.strip()
        if name in ("exit", "quit"):
            return False
        local = getattr(self, f"_cmd_{name}", None)
        if name in LOCAL_COMMANDS and local:
            if name != "clear":
                self._echo_command(line)
            local(args)
            return True

        self._echo_command(line)
        before = self.agent.conversation_id
        result = await self.registry.execute(name, args)
        if result is None:
            self.print(Text(f"Unknown command: /{clean(name)}  (try /help)", "red"))
            return True
        if name == "help":
            result += "\n" + "\n".join(f"/{n} — {d}" for n, d in LOCAL_COMMANDS.items())
        if self.agent.conversation_id != before:
            # Switched conversations: show the new one from the top.
            self.transcript.clear()
            self.show_conversation()
        self.print(Text(clean(result), style="cyan"))
        return True

    def _echo_command(self, line: str) -> None:
        self.print()
        self.print(Text("❯ " + clean(line), style="dim bold"))

    def _cmd_clear(self, _args: str) -> None:
        self.transcript.clear()
        self.scroll_up = 0
        self.invalidate()

    def _cmd_history(self, args: str) -> None:
        try:
            n = int(args) if args else 10
        except ValueError:
            self.print(Text("Usage: /history [n]", "red"))
            return
        self.show_history(n)

    def _cmd_copy(self, _args: str) -> None:
        if not self.last_reply:
            self.print(Text("Nothing to copy yet.", "dim"))
            return
        seq = osc52_copy(self.last_reply)
        if self.app is not None and self.app.is_running:
            self.app.output.write_raw(seq)
            self.app.output.flush()
        else:
            sys.stdout.write(seq)
            sys.stdout.flush()
        self.print(
            Text("Copied the last reply (via your terminal's clipboard).", "dim")
        )

    def _cmd_raw(self, _args: str) -> None:
        self.render_markdown = not self.render_markdown
        mode = "markdown" if self.render_markdown else "raw text"
        self.print(Text(f"Replies render as {mode} (new replies).", "dim"))

    def _cmd_yolo(self, _args: str) -> None:
        on = self.toggle_auto_approve()
        msg = (
            "Auto-approve on: tools run without asking." if on else "Auto-approve off."
        )
        self.print(Text(msg, "yellow" if on else "dim"))

    def _chat_messages(self) -> list[dict]:
        return [
            m
            for m in self.agent.messages
            if m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]

    def show_history(self, n: int, ruled: bool = True) -> None:
        """Print the conversation's last ``n`` user/assistant messages."""
        msgs = self._chat_messages()[-n:] if n > 0 else []
        if not msgs:
            return
        if ruled:
            self.print(Rule(style="dim"))
        for m in msgs:
            self.print()
            if m["role"] == "user":
                self.print(Text("❯ " + clean(m["content"]), style="bold"))
            else:
                self.print(self._render(m["content"]))
        if ruled:
            self.print(Rule(style="dim"))

    def _render(self, text: str):
        text = clean(text)
        return Markdown(text, hyperlinks=False) if self.render_markdown else Text(text)

    # --- main loop -------------------------------------------------------------

    def show_conversation(self) -> None:
        """Header, the conversation's messages so far, and the key hints."""
        conv = self.agent.store.get(self.agent.conversation_id) or {}
        msgs = self._chat_messages()
        self.print(
            Text.assemble(
                ("anotherbot", "bold cyan"),
                ("  ", ""),
                (clean(str(runtime.get("model", "?"))), "dim"),
            )
        )
        count = f" · {len(msgs)} messages" if msgs else ""
        name = clean(conv.get("name", ""))
        self.print(
            Text(f"Conversation #{self.agent.conversation_id} {name}{count}", "dim")
        )
        if len(msgs) > _HISTORY_ON_OPEN:
            self.print(
                Text(
                    f"({len(msgs) - _HISTORY_ON_OPEN} earlier messages not shown)",
                    "dim",
                )
            )
        self.show_history(_HISTORY_ON_OPEN, ruled=False)
        self.print()
        self.print(
            Text(
                "Enter sends · Alt+Enter newline · /help commands · @file attaches · "
                "Shift+Tab auto-approve · Ctrl+C interrupts · Ctrl+D quits",
                "dim",
            )
        )

    async def _worker(self) -> None:
        """Run submitted messages one at a time, in order."""
        while True:
            text = await self._inbox.get()
            try:
                keep_going = await self.handle_input(text)
            except Exception as e:  # pylint: disable=broad-exception-caught  # keep the session alive
                log.debug("Input failed", exc_info=e)
                self.print(Text(f"✗ {type(e).__name__}: {clean(str(e))}", "red"))
                keep_going = True
            if not keep_going:
                self.quit()
                return

    async def run(self, **app_kwargs) -> None:
        """Show the full-screen UI until Ctrl+D, double Ctrl+C or /exit."""
        self._loop = asyncio.get_running_loop()
        self.show_conversation()
        app = self.build_app(**app_kwargs)
        worker = asyncio.ensure_future(self._worker())
        ticker = asyncio.ensure_future(self._tick())
        try:
            await app.run_async()
        finally:
            if self._turn_task is not None:
                self._turn_task.cancel()
            for task in (worker, ticker):
                task.cancel()
            await asyncio.gather(worker, ticker, return_exceptions=True)


def _route_console_logging(tui: Tui) -> None:
    """Show console log output in the TUI, warnings and errors only.

    INFO lines would bury the conversation, and writing to the terminal would
    tear up the full-screen layout; everything still goes to the log file.
    With ``-q`` there is no console handler and nothing is added.
    """
    root = logging.getLogger()
    replaced = False
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            root.removeHandler(handler)
            replaced = True
    if replaced:
        root.addHandler(_ConsoleLogHandler(tui))


async def run_tui(max_iterations: int, auto_approve: bool) -> None:
    """Start the interactive TUI."""
    tui = Tui(max_iterations=max_iterations, auto_approve=auto_approve)
    _route_console_logging(tui)
    await tui.run()
