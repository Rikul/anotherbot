"""Text helpers for the terminal UI: sanitising, summaries, completion and the
transcript (rich renderables rendered to prompt_toolkit lines)."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines
from rich.console import Console

# Commands whose (first) argument is a conversation id.
CONV_ID_COMMANDS = frozenset({"load", "fork", "rename", "export", "delete"})

_RESULT_PREVIEW_CHARS = 160

# C0 controls except tab/newline, DEL, and C1 controls. ESC in particular
# must never reach the terminal from model or tool text: an OSC 52 sequence
# could set the clipboard, others could rewrite the screen or title.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# "@path" attachment tokens: start of text or after whitespace.
_ATTACH_RE = re.compile(r"(?:(?<=\s)|^)@(\S+)")

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
        try:
            path = Path(raw).expanduser()  # "@~nosuchuser" raises RuntimeError
            if not path.is_absolute():
                path = base / path
            is_file = path.is_file()
        except (RuntimeError, OSError, ValueError):
            continue  # not an attachment: leave it as plain text
        if is_file and str(path) not in found:
            found.append(str(path))
    return found


def osc52_copy(text: str) -> str:
    """The OSC 52 escape sequence that puts ``text`` on the terminal's clipboard."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"\033]52;c;{payload}\a"


def _h(text: str) -> str:
    """Escape text for prompt_toolkit HTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
                    display=f"#{cid} {clean(c['name'])}",
                    display_meta=f"{c.get('channel', '')} · {c.get('message_count', 0)} msgs",
                )


# --- output pane -------------------------------------------------------------


class Transcript:
    """The conversation pane's content: rich renderables, rendered to lines.

    Each block is rendered with ``console`` at the pane's width into ANSI
    text, parsed into prompt_toolkit fragments and split into lines. A width
    change re-renders every block, so output re-wraps on resize.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.blocks: list = []
        self.raw: list[str] = []  # each block's ANSI text, as rendered
        self._lines: list[list] = []

    def add(self, renderable) -> None:
        """Append ``renderable`` (rendered at the current width)."""
        self.blocks.append(renderable)
        self._render_block(renderable)

    def clear(self) -> None:
        """Remove everything."""
        self.blocks.clear()
        self.raw.clear()
        self._lines.clear()

    def lines(self, width: int) -> list[list]:
        """The content as lines of fragments, wrapped at ``width``."""
        if width > 0 and width != self.console.width:
            self.console.width = width
            self.raw.clear()
            self._lines.clear()
            for block in self.blocks:
                self._render_block(block)
        return self._lines

    def text(self) -> str:
        """The plain text of everything shown (for tests)."""
        return "\n".join("".join(f[1] for f in line) for line in self._lines)

    def _render_block(self, renderable) -> None:
        with self.console.capture() as capture:
            self.console.print(renderable)
        rendered = capture.get()
        self.raw.append(rendered)
        # The trailing newline leaves an empty last "line": drop it.
        self._lines.extend(list(split_lines(to_formatted_text(ANSI(rendered))))[:-1])
