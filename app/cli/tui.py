"""Interactive terminal UI for the CLI agent (prompt_toolkit input, rich output).

Not full-screen: output goes to the normal scrollback, and only the input
line and a one-line status bar are managed. Features:

- line editing, persistent history (Ctrl+R search), multi-line input
  (Alt+Enter / Ctrl+J, or end a line with ``\\``), pasting multi-line text;
- completion for slash commands, conversation ids after ``/load`` & co., and
  ``@path`` file attachments;
- markdown-rendered replies (``/raw`` toggles), a spinner with elapsed time
  and the running tool, compact tool call/result lines, per-turn timing and
  token counts;
- single-key tool permission prompts (yes / no / always for this tool /
  approve everything), Shift+Tab toggles auto-approve;
- Ctrl+C interrupts a running turn; at the prompt it clears the line, and
  twice in a row (or Ctrl+D) quits.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import signal
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from .. import config
from ..channels.commands import CommandRegistry, build_command_registry
from ..core import runtime
from ..infra.app_logging import log
from .cli_agent import CliAgent

HISTORY_FILE = config.PROJECT_HOME / "cli_history"

# Commands whose (first) argument is a conversation id.
CONV_ID_COMMANDS = frozenset({"load", "fork", "rename", "export", "delete"})

# Commands the TUI handles itself (the rest go to the shared CommandRegistry).
LOCAL_COMMANDS = {
    "clear": "Clear the screen.",
    "history": "Show recent messages of this conversation. Usage: /history [n]",
    "copy": "Copy the last reply to the clipboard.",
    "raw": "Toggle markdown rendering of replies.",
    "yolo": "Toggle auto-approve for tool calls (also Shift+Tab).",
    "exit": "Quit.",
    "quit": "Quit.",
}

_EXIT_CONFIRM_SECS = 2.0
_RESULT_PREVIEW_CHARS = 160

# C0 controls except tab/newline, DEL, and C1 controls. ESC in particular
# must never reach the terminal from model or tool text: an OSC 52 sequence
# could set the clipboard, others could rewrite the screen or title.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# "@path" attachment tokens: start of text or after whitespace.
_ATTACH_RE = re.compile(r"(?:(?<=\s)|^)@(\S+)")

STYLE = Style.from_dict(
    {
        "prompt": "ansicyan bold",
        "continuation": "ansibrightblack",
        "bottom-toolbar": "noreverse bg:default ansibrightblack",
        "bottom-toolbar.key": "ansicyan",
        "bottom-toolbar.warn": "ansiyellow bold",
        "bottom-toolbar.ok": "ansigreen",
        "perm.title": "ansiyellow bold",
        "perm.key": "ansicyan bold",
    }
)


# --- helpers -----------------------------------------------------------------


def clean(text: str) -> str:
    """``text`` without terminal control characters (tabs and newlines kept)."""
    return _CONTROL_RE.sub("", text)


def one_line(value, limit: int) -> str:
    """``value`` as a single whitespace-collapsed line of at most ``limit`` chars."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(clean(text).split())
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def summarize_args(args: dict, limit: int) -> str:
    """A compact one-line view of tool arguments (just the value if there is one)."""
    if not args:
        return ""
    if len(args) == 1:
        return one_line(next(iter(args.values())), limit)
    parts = [f"{k}={one_line(v, limit)}" for k, v in args.items()]
    return one_line(", ".join(parts), limit)


def summarize_result(result: str, limit: int = _RESULT_PREVIEW_CHARS) -> str:
    """First non-empty line of a tool result, plus how many lines follow."""
    lines = [ln for ln in (result or "").splitlines() if ln.strip()]
    if not lines:
        return "(no output)"
    head = one_line(lines[0], limit)
    return head + (f"  (+{len(lines) - 1} lines)" if len(lines) > 1 else "")


def format_tokens(n: int) -> str:
    """1234 -> '1.2k', 1_500_000 -> '1.5M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def extract_attachments(text: str, cwd: Path | None = None) -> list[str]:
    """Paths of existing files referenced as ``@path`` in ``text`` (deduplicated)."""
    base = cwd or Path.cwd()
    found: list[str] = []
    for match in _ATTACH_RE.finditer(text):
        raw = match.group(1).rstrip(".,;:!?)")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base / path
        if path.is_file() and str(path) not in found:
            found.append(str(path))
    return found


def osc52_copy(text: str) -> str:
    """The OSC 52 escape sequence that puts ``text`` on the terminal's clipboard."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"\033]52;c;{payload}\a"


class TuiCompleter(Completer):
    """Completes ``/commands``, conversation ids after them, and ``@paths``.

    Args:
        commands: returns ``(name, description)`` pairs.
        conversations: returns conversation rows (``id``, ``name``,
            ``channel``, ``message_count``), most recent first.
    """

    def __init__(
        self,
        commands: Callable[[], Iterable[tuple[str, str]]],
        conversations: Callable[[], list[dict]],
    ) -> None:
        self._commands = commands
        self._conversations = conversations
        self._paths = PathCompleter(expanduser=True)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and "\n" not in text:
            yield from self._slash_completions(text)
            return
        word = document.get_word_before_cursor(WORD=True)
        if word.startswith("@"):
            sub = Document(word[1:], len(word) - 1)
            yield from self._paths.get_completions(sub, complete_event)

    def _slash_completions(self, text: str):
        name, sep, rest = text[1:].partition(" ")
        if not sep:
            for cmd, desc in self._commands():
                if cmd.startswith(name.lower()):
                    yield Completion(
                        "/" + cmd,
                        start_position=-len(text),
                        display="/" + cmd,
                        display_meta=desc,
                    )
            return
        if name.lower() not in CONV_ID_COMMANDS or " " in rest:
            return
        try:
            convs = self._conversations()
        except Exception:  # pylint: disable=broad-exception-caught  # completion is best-effort
            return
        for c in convs:
            cid = str(c["id"])
            if cid.startswith(rest):
                yield Completion(
                    cid,
                    start_position=-len(rest),
                    display=f"#{cid} {c['name']}",
                    display_meta=f"{c.get('channel', '')} · {c.get('message_count', 0)} msgs",
                )


class _ConsoleLogHandler(logging.Handler):
    """Logs warnings and errors through the rich console, so they don't tear
    up the spinner or the prompt."""

    def __init__(self, console: Console) -> None:
        super().__init__(logging.WARNING)
        self.console = console

    def emit(self, record: logging.LogRecord) -> None:
        try:
            style = "red" if record.levelno >= logging.ERROR else "yellow"
            self.console.print(
                Text(
                    f"{record.levelname.lower()}: {clean(record.getMessage())}",
                    style=style,
                )
            )
        except Exception:  # pylint: disable=broad-exception-caught  # logging must never raise
            self.handleError(record)


# --- the TUI -----------------------------------------------------------------


class Tui:  # pylint: disable=too-many-instance-attributes
    """The interactive session: reads input, runs commands and agent turns.

    Args:
        max_iterations: passed to the ``CliAgent``.
        auto_approve: start with tool calls auto-approved.
        console: rich console to write to (tests pass a recording one).
        history_file: where input history is kept; ``None`` keeps it in memory.
    """

    def __init__(
        self,
        max_iterations: int = 250,
        auto_approve: bool = False,
        console: Console | None = None,
        history_file: Path | None = HISTORY_FILE,
    ) -> None:
        self.console = console or Console(highlight=False)
        self.agent = CliAgent(
            max_iterations=max_iterations, auto_approve=auto_approve, ui=self
        )
        self.registry: CommandRegistry = build_command_registry(self.agent)
        self.render_markdown = True
        self.last_reply = ""
        self.allowed_tools: set[str] = set()
        self.session_tokens = {"in": 0, "out": 0}
        self._status = None  # rich Status while a turn runs
        self._activity = ""  # what the spinner says we're doing
        self._turn_started = 0.0
        self._perm_lock = asyncio.Lock()
        self._exit_armed_at = 0.0
        self._toast = ""  # short message shown in the status bar…
        self._toast_until = 0.0  # …until this time.monotonic()
        self._session = PromptSession(
            history=self._history(history_file),
            completer=TuiCompleter(self._command_list, self._conversation_list),
            complete_while_typing=True,
            multiline=True,
            key_bindings=self._key_bindings(),
            prompt_continuation=lambda width, *_: HTML(
                "<continuation>" + "…".rjust(width - 1) + " </continuation>"
            ),
            bottom_toolbar=self._toolbar,
            style=STYLE,
            refresh_interval=1.0,
            enable_history_search=False,
            reserve_space_for_menu=6,
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

    # --- completion sources --------------------------------------------------

    def _command_list(self) -> list[tuple[str, str]]:
        cmds = {c.name: c.description for c in self.registry.list()}
        cmds.update(LOCAL_COMMANDS)
        return sorted(cmds.items())

    def _conversation_list(self) -> list[dict]:
        return self.agent.store.list()

    # --- key bindings and status bar -----------------------------------------

    def _key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
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
            if buf.text.strip():
                buf.validate_and_handle()

        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def _ctrl_c(event):
            buf = event.current_buffer
            if buf.text:
                buf.reset()
                return
            now = time.monotonic()
            if now - self._exit_armed_at < _EXIT_CONFIRM_SECS:
                event.app.exit(exception=EOFError())
                return
            self._exit_armed_at = now
            self.toast("Press Ctrl+C again to quit", _EXIT_CONFIRM_SECS)

        @kb.add("s-tab")
        def _toggle_approve(_event):
            self.toggle_auto_approve()

        return kb

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

    def _toolbar(self):
        model = runtime.get("model", "?")
        conv_id = runtime.get("conversation_id", "—")
        conv_name = runtime.get("conversation_name", "")
        approve = (
            "<warn>auto-approve</warn>"
            if self.agent.auto_approve
            else "<ok>ask before tools</ok>"
        )
        toks = self.session_tokens["in"] + self.session_tokens["out"]
        parts = [
            f" {_h(model)}",
            f"#{conv_id} {_h(one_line(conv_name, 40))}",
            approve,
        ]
        if toks:
            parts.append(f"{format_tokens(toks)} tok")
        if not self.render_markdown:
            parts.append("raw")
        left = "  │  ".join(parts)
        if self._toast and time.monotonic() < self._toast_until:
            return HTML(f"{left}  │  <warn>{_h(self._toast)}</warn>")
        hints = (
            "<key>/</key> cmds  <key>@</key> attach  <key>⇧Tab</key> approve  "
            "<key>Alt+⏎</key> newline"
        )
        return HTML(f"{left}  │  {hints}")

    # --- CliUI hooks (called by CliAgent) --------------------------------------

    def thinking(self, text: str) -> None:
        """Assistant text written alongside tool calls."""
        self.console.print(Text(clean(text), style="dim italic"))

    def tool_start(self, name: str, args: dict) -> None:
        """One line per tool call; also shown next to the spinner."""
        width = max(20, self.console.width - len(name) - 6)
        summary = summarize_args(args, width)
        line = Text("⏺ ", style="cyan")
        line.append(clean(name), style="bold cyan")
        if summary:
            line.append(f"  {summary}", style="dim")
        self.console.print(line)
        self._set_activity(f"{name} {one_line(summary, 40)}".strip())

    def tool_result(self, name: str, result: str) -> None:
        """A dim one-line preview of what the tool returned."""
        failed = (result or "").startswith(
            (f"Error running tool {name}", "User denied permission")
        )
        self.console.print(
            Text("  ⎿ " + summarize_result(result), style="red" if failed else "dim")
        )
        self._set_activity("thinking")

    def response(self, text: str) -> None:
        """The final reply, as markdown unless /raw is on."""
        self.last_reply = text.strip()
        self.console.print()
        self.console.print(self._render(self.last_reply))

    async def ask_permission(self, name: str, args: dict) -> bool:
        """Single-key prompt: y/Enter yes, n/Esc no, a always this tool, ! everything."""
        if name in self.allowed_tools or self.agent.auto_approve:
            return True
        async with self._perm_lock:  # parallel tool calls ask one at a time
            if name in self.allowed_tools or self.agent.auto_approve:
                return True
            self._pause_spinner()
            try:
                self._print_permission_request(name, args)
                answer = await self._read_permission_key()
            finally:
                self._resume_spinner()
        if answer == "always":
            self.allowed_tools.add(name)
            self.console.print(
                Text(f"  ✓ {clean(name)} allowed for this session", "green")
            )
        elif answer == "all":
            self.agent.auto_approve = True
            self.console.print(
                Text("  ✓ auto-approve on (Shift+Tab to turn off)", "yellow")
            )
        elif answer == "no":
            self.console.print(Text("  ✗ denied", style="red"))
        return answer != "no"

    def _print_permission_request(self, name: str, args: dict) -> None:
        self.console.print()
        self.console.print(
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
                self.console.print(Text(f"  {clean(key)}:", style="dim"))
                self.console.print(Text("    " + clean(text).replace("\n", "\n    ")))
            else:
                self.console.print(
                    Text.assemble((f"  {clean(key)}: ", "dim"), clean(text))
                )

    async def _read_permission_key(self) -> str:
        kb = KeyBindings()
        answers = {
            "y": "yes", "Y": "yes", "enter": "yes",
            "n": "no", "N": "no", "escape": "no", "c-c": "no", "c-d": "no",
            "a": "always", "A": "always",
            "!": "all",
        }  # fmt: skip
        for key, value in answers.items():
            kb.add(key, eager=True)(lambda e, v=value: e.app.exit(result=v))

        @kb.add("<any>")
        def _ignore(_event):
            pass

        prompt = HTML(
            "  <perm.key>y</perm.key>es  <perm.key>n</perm.key>o  "
            "<perm.key>a</perm.key>lways this tool  "
            "<perm.key>!</perm.key> approve all  "
        )
        session: PromptSession = PromptSession(key_bindings=kb, style=STYLE)
        try:
            with patch_stdout(raw=True):
                return await session.prompt_async(prompt) or "no"
        except (KeyboardInterrupt, EOFError):
            return "no"

    # --- spinner ---------------------------------------------------------------

    def _set_activity(self, activity: str) -> None:
        self._activity = activity
        self._refresh_spinner()

    def _refresh_spinner(self) -> None:
        if self._status is not None:
            elapsed = int(time.monotonic() - self._turn_started)
            self._status.update(
                f"[cyan]{escape(self._activity)}[/cyan] [dim]{elapsed}s · Ctrl+C to interrupt[/dim]"
            )

    def _pause_spinner(self) -> None:
        if self._status is not None:
            self._status.stop()

    def _resume_spinner(self) -> None:
        if self._status is not None:
            self._status.start()

    async def _tick_spinner(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            self._refresh_spinner()

    # --- turns -----------------------------------------------------------------

    async def run_turn(self, text: str) -> None:
        """Run one agent turn with a spinner; Ctrl+C cancels it."""
        files = extract_attachments(text)
        metadata = {"files": files} if files else None
        for f in files:
            self.console.print(Text(f"  📎 {Path(f).name}", style="dim"))

        self._turn_started = time.monotonic()
        self._activity = "thinking"
        task = asyncio.ensure_future(self.agent.agent_loop(text, metadata))
        ticker = asyncio.ensure_future(self._tick_spinner())
        loop = asyncio.get_running_loop()
        old_handler = signal.getsignal(signal.SIGINT)
        handler_set = False
        try:
            loop.add_signal_handler(signal.SIGINT, task.cancel)
            handler_set = True
        except (NotImplementedError, RuntimeError, ValueError):
            pass  # e.g. not the main thread: Ctrl+C keeps its default behaviour
        try:
            with self.console.status("", spinner="dots") as status:
                self._status = status
                self._refresh_spinner()
                await asyncio.wait({task})
        finally:
            self._status = None
            ticker.cancel()
            if handler_set:
                loop.remove_signal_handler(signal.SIGINT)
                signal.signal(signal.SIGINT, old_handler)

        if task.cancelled():
            self.console.print(Text("⏹ Interrupted", style="yellow"))
        elif task.exception() is not None:
            err = task.exception()
            log.debug("Turn failed", exc_info=err)
            self.console.print(
                Text(f"✗ {type(err).__name__}: {clean(str(err))}", style="red")
            )
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
        self.console.print(Text("  " + " · ".join(parts), style="dim"))
        self.console.print()

    # --- commands --------------------------------------------------------------

    async def run_command(self, line: str) -> bool:
        """Run a slash command. Returns False when the session should end."""
        name, _, args = line[1:].partition(" ")
        name = name.lower()
        args = args.strip()
        if name in ("exit", "quit"):
            return False
        local = getattr(self, f"_cmd_{name}", None)
        if name in LOCAL_COMMANDS and local:
            local(args)
            return True

        before = self.agent.conversation_id
        result = await self.registry.execute(name, args)
        if result is None:
            self.console.print(
                Text(f"Unknown command: /{clean(name)}  (try /help)", "red")
            )
            return True
        if name == "help":
            result += "\n" + "\n".join(f"/{n} — {d}" for n, d in LOCAL_COMMANDS.items())
        self.console.print(Text(clean(result)))
        if self.agent.conversation_id != before:
            self.show_history(4)
        return True

    def _cmd_clear(self, _args: str) -> None:
        self.console.clear()

    def _cmd_history(self, args: str) -> None:
        try:
            n = int(args) if args else 10
        except ValueError:
            self.console.print(Text("Usage: /history [n]", "red"))
            return
        self.show_history(n)

    def _cmd_copy(self, _args: str) -> None:
        if not self.last_reply:
            self.console.print(Text("Nothing to copy yet.", "dim"))
            return
        sys.stdout.write(osc52_copy(self.last_reply))
        sys.stdout.flush()
        self.console.print(
            Text("Copied the last reply (via your terminal's clipboard).", "dim")
        )

    def _cmd_raw(self, _args: str) -> None:
        self.render_markdown = not self.render_markdown
        mode = "markdown" if self.render_markdown else "raw text"
        self.console.print(Text(f"Replies render as {mode}.", "dim"))

    def _cmd_yolo(self, _args: str) -> None:
        on = self.toggle_auto_approve()
        msg = (
            "Auto-approve on: tools run without asking." if on else "Auto-approve off."
        )
        self.console.print(Text(msg, "yellow" if on else "dim"))

    def show_history(self, n: int) -> None:
        """Print the conversation's last ``n`` user/assistant messages."""
        msgs = (
            [
                m
                for m in self.agent.messages
                if m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)
            ][-n:]
            if n > 0
            else []
        )
        if not msgs:
            return
        self.console.rule(style="dim")
        for m in msgs:
            if m["role"] == "user":
                self.console.print(Text("❯ " + clean(m["content"]), style="bold"))
            else:
                self.console.print(self._render(m["content"]))
            self.console.print()
        self.console.rule(style="dim")

    def _render(self, text: str):
        text = clean(text)
        return Markdown(text) if self.render_markdown else Text(text)

    # --- main loop -------------------------------------------------------------

    def print_banner(self) -> None:
        """Model, conversation and the key hints."""
        conv = self.agent.store.get(self.agent.conversation_id) or {}
        n = len(
            [m for m in self.agent.messages if m.get("role") in ("user", "assistant")]
        )
        self.console.print(
            Text.assemble(
                ("anotherbot", "bold cyan"),
                ("  ", ""),
                (runtime.get("model", "?"), "dim"),
            )
        )
        resumed = f" · {n} messages" if n else ""
        name = clean(conv.get("name", ""))
        self.console.print(
            Text(f"Conversation #{self.agent.conversation_id} {name}{resumed}", "dim")
        )
        self.console.print(
            Text(
                "Enter sends · Alt+Enter newline · /help commands · @file attaches · "
                "Shift+Tab auto-approve · Ctrl+C interrupts · Ctrl+D quits",
                "dim",
            )
        )
        if n:
            self.show_history(2)
        else:
            self.console.print()

    async def read_input(self) -> str:
        """Read one message; raises EOFError when the user quits."""
        with patch_stdout(raw=True):
            return await self._session.prompt_async(HTML("<prompt>❯ </prompt>"))

    async def run(self) -> None:
        """The read–eval loop, until Ctrl+D, double Ctrl+C or /exit."""
        self.print_banner()
        while True:
            try:
                text = (await self.read_input()).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text.startswith("/") and not text.startswith("//"):
                if not await self.run_command(text):
                    break
            else:
                # "//" escapes a message that really starts with a slash
                await self.run_turn(text[1:] if text.startswith("//") else text)
        self.console.print(Text("Bye.", style="dim"))


def _h(text: str) -> str:
    """Escape text for prompt_toolkit HTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _quiet_console_logging(console: Console) -> None:
    """Route console log output through rich, warnings and errors only.

    INFO lines would bury the conversation; they still go to the log file.
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
        root.addHandler(_ConsoleLogHandler(console))


async def run_tui(max_iterations: int, auto_approve: bool) -> None:
    """Start the interactive TUI."""
    tui = Tui(max_iterations=max_iterations, auto_approve=auto_approve)
    _quiet_console_logging(tui.console)
    await tui.run()
