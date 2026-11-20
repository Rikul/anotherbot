"""Read-only file system tools: ``list_dir``, ``glob`` and ``grep``.

They replace the ``ls``, ``find`` and ``grep`` calls the model would otherwise
run through ``bash``: no shell, nothing written, and (being ``read_only``)
no permission prompt in the CLI.
"""

import os
import re
import stat
import time
from datetime import datetime

from ..core.tool import Tool
from ..infra.app_logging import log

# Directories glob and grep never descend into: VCS data, virtualenvs,
# dependency and cache folders. They are huge and almost never what a search
# is after; list_dir still shows them, and a path inside one can be given.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
    }
)

MAX_LIST_ENTRIES = 1000
MAX_GLOB_RESULTS = 500
DEFAULT_GREP_RESULTS = 200
MAX_GREP_RESULTS = 1000
MAX_GREP_LINE = 300  # characters of a matching line shown
MAX_GREP_FILE_BYTES = 5 * 1024 * 1024  # bigger files are skipped
# glob and grep stop walking after this long and say so: threaded tools run
# one at a time, so a walk of "/" must not hold up every later tool call.
WALK_TIME_BUDGET_SECS = 20.0
_BINARY_SNIFF = 8192


def glob_to_regex(pattern: str) -> re.Pattern:
    """Compile a glob (``*``, ``?``, ``[...]``, ``**``) matching ``/``-separated paths.

    ``*`` and ``?`` stay within one path component; ``**/`` matches zero or
    more directories and a bare ``**`` anything.
    """
    out = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            negated = pattern[i + 1 : i + 2] in ("!", "^")
            # A "]" right after "[" (or "[!") is part of the set, as in fnmatch.
            end = pattern.find("]", i + (3 if negated else 2))
            if end == -1:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + (2 if negated else 1) : end].replace("\\", "\\\\")
                out.append(f"[{'^' if negated else ''}{body}]")
                i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z", re.DOTALL)


class _PathMatcher:  # pylint: disable=too-few-public-methods  # one predicate
    """Match a relative path against a glob: a pattern without ``/`` matches
    the file name at any depth (``*.py``), one with ``/`` the whole path."""

    def __init__(self, pattern: str):
        pattern = pattern.replace("\\", "/").removeprefix("./")
        self.basename_only = "/" not in pattern
        self.regex = glob_to_regex(pattern)

    def matches(self, rel_path: str) -> bool:
        """True if ``rel_path`` (``/``-separated, relative to the root) matches."""
        target = rel_path.rsplit("/", 1)[-1] if self.basename_only else rel_path
        return self.regex.match(target) is not None


class _Walk:  # pylint: disable=too-few-public-methods  # an iterable with a flag
    """Iterate (absolute path, ``/``-separated path relative to ``root``) for
    every file under ``root``, in sorted order, skipping ``SKIP_DIRS``.

    Stops after ``WALK_TIME_BUDGET_SECS`` and sets ``timed_out``.
    """

    def __init__(self, root: str):
        self.root = root
        self.timed_out = False

    def __iter__(self):
        deadline = time.monotonic() + WALK_TIME_BUDGET_SECS
        for dirpath, dirnames, filenames in os.walk(self.root):
            if time.monotonic() > deadline:  # also for trees of empty dirs
                self.timed_out = True
                return
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            rel_dir = os.path.relpath(dirpath, self.root).replace(os.sep, "/")
            for name in sorted(filenames):
                if time.monotonic() > deadline:
                    self.timed_out = True
                    return
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                yield os.path.join(dirpath, name), rel


def _timed_out_note(walk) -> list[str]:
    if getattr(walk, "timed_out", False):
        return [
            f"… stopped after {WALK_TIME_BUDGET_SECS:.0f}s, so results are "
            "incomplete; narrow the path"
        ]
    return []


def _compile_glob(pattern: str):
    """A _PathMatcher, or an error text for an invalid pattern."""
    try:
        return _PathMatcher(pattern)
    except re.error as e:
        return f"Error: invalid glob pattern {pattern!r}: {e}"


def _format_size(size: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{size}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size}"  # unreachable


class ListDirTool(Tool):
    """List a directory: directories first (trailing ``/``), then files with size."""

    read_only = True

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": (
                    "List the entries of a directory (like `ls -la`): directories end "
                    "with '/', files show their size and modification time. Read-only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory to list (default: the working directory)",
                        },
                        "show_hidden": {
                            "type": "boolean",
                            "description": "Include entries starting with '.' (default: true)",
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(path: str = ".", show_hidden: bool = True) -> str:
        """Return one line per entry; errors as text."""
        log.info("list_dir, path: %s", path)
        path = os.path.expanduser(path or ".")
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except FileNotFoundError:
            return f"Error: directory {path} does not exist"
        except NotADirectoryError:
            return f"Error: {path} is not a directory"
        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            return f"Error listing {path}: {e}"

        if not show_hidden:
            entries = [e for e in entries if not e.name.startswith(".")]
        dirs, files = [], []
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            (dirs if is_dir else files).append(entry)
        dirs.sort(key=lambda e: e.name)
        files.sort(key=lambda e: e.name)

        lines = [f"{os.path.abspath(path)}: {len(dirs)} dirs, {len(files)} files"]
        for entry in (dirs + files)[:MAX_LIST_ENTRIES]:
            lines.append(_describe_entry(entry))
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append(f"… {len(entries) - MAX_LIST_ENTRIES} more entries not shown")
        return "\n".join(lines)


def _describe_entry(entry: os.DirEntry) -> str:
    link = ""
    if entry.is_symlink():
        try:
            link = f" -> {os.readlink(entry.path)}"
        except OSError:
            link = " -> ?"
    try:
        st = entry.stat()
    except OSError:
        return f"{entry.name}{link}  (unreadable)"
    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    if stat.S_ISDIR(st.st_mode):
        return f"{entry.name}/{link}  {mtime}"
    return f"{entry.name}{link}  {_format_size(st.st_size)}  {mtime}"


class GlobTool(Tool):
    """Find files whose path matches a glob pattern."""

    read_only = True

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "glob",
                "description": (
                    "Find files by name pattern (instead of `find`). A pattern without "
                    "'/' matches file names at any depth ('*.py'); one with '/' matches "
                    "the path relative to `path` ('src/**/*.ts', 'tests/test_*.py'). "
                    f"Skips {', '.join(sorted(SKIP_DIRS))}. Read-only."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["pattern"],
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern"},
                        "path": {
                            "type": "string",
                            "description": "Directory to search (default: the working directory)",
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(pattern: str, path: str = ".") -> str:
        """Return matching paths relative to ``path``, one per line."""
        log.info("glob, pattern: %s, path: %s", pattern, path)
        root = os.path.expanduser(path or ".")
        if not os.path.isdir(root):
            return f"Error: directory {root} does not exist"
        matcher = _compile_glob(pattern)
        if isinstance(matcher, str):
            return matcher
        found = []
        walk = _Walk(root)
        for _, rel in walk:
            if matcher.matches(rel):
                found.append(rel)
                if len(found) > MAX_GLOB_RESULTS:
                    break
        if not found:
            return "\n".join(
                [f"No files matching {pattern!r} under {root}", *_timed_out_note(walk)]
            )
        if len(found) > MAX_GLOB_RESULTS:
            return "\n".join(
                found[:MAX_GLOB_RESULTS]
                + [
                    f"… stopped after {MAX_GLOB_RESULTS} files; narrow the pattern or path"
                ]
            )
        return "\n".join(found + _timed_out_note(walk))


class GrepTool(Tool):
    """Search file contents for a regular expression."""

    read_only = True

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "grep",
                "description": (
                    "Search file contents for a regular expression (Python syntax), "
                    "instead of `grep -rn`. Returns 'path:line: text' for each matching "
                    "line. `path` may be a file or a directory (searched recursively, "
                    "skipping binary files and "
                    f"{', '.join(sorted(SKIP_DIRS))}). Read-only."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["pattern"],
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Regular expression",
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "File or directory to search "
                                "(default: the working directory)"
                            ),
                        },
                        "glob": {
                            "type": "string",
                            "description": (
                                "Only search files matching this glob, "
                                "e.g. '*.py' (see the glob tool)"
                            ),
                        },
                        "ignore_case": {
                            "type": "boolean",
                            "description": "Case-insensitive match (default: false)",
                        },
                        "files_only": {
                            "type": "boolean",
                            "description": (
                                "Return only the paths of matching files "
                                "(default: false)"
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum matching lines (or files) to return; default "
                                f"{DEFAULT_GREP_RESULTS}, max {MAX_GREP_RESULTS}"
                            ),
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # tool parameters
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        ignore_case: bool = False,
        files_only: bool = False,
        max_results: int = DEFAULT_GREP_RESULTS,
    ) -> str:
        """Return matches as ``path:line: text`` lines; errors as text."""
        log.info("grep, pattern: %s, path: %s, glob: %s", pattern, path, glob)
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            return f"Error: invalid regular expression: {e}"
        try:
            limit = max(
                1, min(int(max_results or DEFAULT_GREP_RESULTS), MAX_GREP_RESULTS)
            )
        except (TypeError, ValueError):
            limit = DEFAULT_GREP_RESULTS

        root = os.path.expanduser(path or ".")
        if os.path.isfile(root):
            files = [(root, root)]
        elif os.path.isdir(root):
            files = _Walk(root)
        else:
            return f"Error: {root} does not exist"
        matcher = _compile_glob(glob) if glob else None
        if isinstance(matcher, str):
            return matcher

        results, truncated = _search(files, matcher, regex, files_only, limit)
        if not results:
            return "\n".join(
                [f"No matches for {pattern!r} under {root}", *_timed_out_note(files)]
            )
        out = results[:limit] + _timed_out_note(files)
        if truncated:
            out.append(
                f"… stopped at {limit} results (there may be more); "
                "narrow the pattern, path or glob"
            )
        return "\n".join(out)


def _search(files, matcher, regex, files_only: bool, limit: int):
    """(result lines, whether more than ``limit`` were found).

    Stops at the first result past ``limit``, so exactly ``limit`` results
    are not reported as cut short.
    """
    results: list[str] = []
    for abs_path, rel in files:
        if matcher and not matcher.matches(rel):
            continue
        hits = _grep_file(abs_path, regex)
        if not hits:
            continue
        if files_only:
            results.append(rel)
        else:
            results.extend(f"{rel}:{n}: {line}" for n, line in hits)
        if len(results) > limit:
            return results, True
    return results, False


def _grep_file(path: str, regex: re.Pattern) -> list[tuple[int, str]]:
    """Matching (line number, line) pairs; [] for unreadable or binary files.

    Only regular files are read: a FIFO would block on open and a device
    such as /dev/zero would never stop (their size reads as 0).
    """
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_GREP_FILE_BYTES:
            return []
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    if b"\0" in data[:_BINARY_SNIFF]:
        return []
    hits = []
    text = data.decode("utf-8", errors="replace")
    # Split on "\n" only: splitlines() also breaks at form feeds, \x1c-\x1e,
    # \u2028 …, which would make the line numbers disagree with editors.
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()  # no line after the final newline
    for n, line in enumerate(lines, 1):
        line = line.removesuffix("\r")
        if regex.search(line):
            if len(line) > MAX_GREP_LINE:
                line = line[:MAX_GREP_LINE] + "…"
            hits.append((n, line))
    return hits
