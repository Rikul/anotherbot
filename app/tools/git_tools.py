"""Read-only git tools: ``git_status``, ``git_diff`` and ``git_log``.

They run git directly (no shell) with options that keep it from changing
anything or running repository-configured programs: no optional index locks,
no fsmonitor hook, no external diff or textconv drivers, no clean/process
filters (they run when the work tree is compared with the index), no
signature checks (gpg), no recursion into submodules (whose own config could
run programs), no pager or prompts.
"""

import os
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass

from ..core.tool import Tool
from ..infra.app_logging import log

GIT_TIMEOUT_SECS = 30
MAX_LOG_COUNT = 200
# Output past this is not read (git is stopped): the model only gets
# MAX_TOOL_RESULT_LENGTH characters anyway, and a 200-commit patch log could
# otherwise put hundreds of MB in memory.
MAX_GIT_OUTPUT_BYTES = 1024 * 1024

_GIT_ENV = {
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",  # status must not rewrite .git/index
}


_SAFE_CONFIG = [
    "core.fsmonitor=false",  # a configured fsmonitor runs a program
    "log.showSignature=false",  # would run gpg.program
    "color.ui=false",
]


class _GitError(Exception):
    """A git invocation failed before it produced a result (text for the model)."""


@dataclass
class _Result:
    returncode: int
    stdout: bytes
    stderr: bytes
    truncated: bool  # stdout reached MAX_GIT_OUTPUT_BYTES and git was stopped


def _kill(proc: subprocess.Popen) -> None:
    """Kill git and anything it started (they could hold the pipe open)."""
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def _exec(cwd: str, args: list[str]) -> _Result:
    """Run git, reading at most MAX_GIT_OUTPUT_BYTES of stdout."""
    try:
        with tempfile.TemporaryFile() as err_file:
            with subprocess.Popen(
                ["git", "-C", cwd, "--no-pager", *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err_file,  # a file, so a chatty stderr can't block git
                env={**os.environ, **_GIT_ENV},
                start_new_session=os.name != "nt",
            ) as proc:
                fired = threading.Event()  # set before the kill, so no race

                def on_timeout(proc=proc):
                    fired.set()
                    _kill(proc)

                timer = threading.Timer(GIT_TIMEOUT_SECS, on_timeout)
                timer.start()
                try:
                    out = proc.stdout.read(MAX_GIT_OUTPUT_BYTES + 1)
                    truncated = len(out) > MAX_GIT_OUTPUT_BYTES
                    if truncated:
                        _kill(proc)
                    proc.wait()
                finally:
                    timer.cancel()
                timed_out = fired.is_set() and not truncated
            err_file.seek(0)
            err = err_file.read(64 * 1024)
    except FileNotFoundError as e:
        raise _GitError("Error: git is not installed") from e
    except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
        raise _GitError(f"Error running git: {e}") from e
    if timed_out and proc.returncode != 0:
        raise _GitError(f"Error: git timed out after {GIT_TIMEOUT_SECS} seconds")
    return _Result(proc.returncode, out[:MAX_GIT_OUTPUT_BYTES], err, truncated)


def _filter_overrides(cwd: str) -> list[str]:
    """``-c`` options that blank every configured filter driver.

    A ``filter.<name>.clean`` or ``.process`` command runs whenever git
    compares a work-tree file with the index (diff, and status for files
    whose stat data changed), so it must be off for these tools to be
    read-only. Reading the config itself runs nothing.
    """
    proc = _exec(cwd, ["config", "-z", "--get-regexp", r"^filter\."])
    if proc.truncated:  # a filter past the cap would stay enabled
        raise _GitError("Error: refusing to run git: filter config too large to check")
    if proc.returncode not in (0, 1):  # 1: no filter configured
        err = proc.stderr.decode(errors="replace").strip()
        raise _GitError(f"Error: could not read git config: {err}")
    names = set()
    for item in proc.stdout.decode(errors="replace").split("\0"):
        key = item.split("\n", 1)[0]
        if key.startswith("filter."):
            names.add(key[len("filter.") :].rpartition(".")[0])
    overrides = []
    for name in sorted(names):
        if "=" in name:  # "-c" splits at the first "=": can't override it
            raise _GitError(f"Error: refusing to run git: odd filter name {name!r}")
        for var, value in (("clean", ""), ("process", ""), ("required", "false")):
            overrides += ["-c", f"filter.{name}.{var}={value}"]
    return overrides


def run_git(path: str, args: list[str]) -> str:
    """Run ``git -C path <args>`` and return stdout (stderr appended); errors as text."""
    cwd = os.path.expanduser(path or ".")
    if not os.path.isdir(cwd):
        return f"Error: directory {cwd} does not exist"
    try:
        options = [opt for c in _SAFE_CONFIG for opt in ("-c", c)]
        options += _filter_overrides(cwd)
        proc = _exec(cwd, [*options, *args])
    except _GitError as e:
        return str(e)
    out = proc.stdout.decode(errors="replace")
    err = proc.stderr.decode(errors="replace").strip()
    if proc.truncated:
        return out + f"\n… output cut at {MAX_GIT_OUTPUT_BYTES} bytes"
    if proc.returncode != 0:
        return f"Error (git exit {proc.returncode}): {err or out}".rstrip()
    if err:
        out += f"\n[stderr]\n{err}"
    return out


def _check_ref(ref: str | None) -> str | None:
    """An error if ``ref`` could be read as an option (``--output=…`` writes files)."""
    if ref and ref.lstrip().startswith("-"):
        return f"Error: invalid ref {ref!r}"
    return None


_PATH_PROP = {
    "type": "string",
    "description": "A directory inside the repository (default: the working directory)",
}
_FILE_PROP = {
    "type": "string",
    "description": "Limit to this file or directory (relative to `path`)",
}


class GitStatusTool(Tool):
    """``git status --short --branch``."""

    read_only = True

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "git_status",
                "description": (
                    "Show the branch, its upstream state and changed/untracked files "
                    "(`git status --short --branch`). Read-only."
                ),
                "parameters": {"type": "object", "properties": {"path": _PATH_PROP}},
            },
        }

    @staticmethod
    def call(path: str = ".") -> str:
        """Short status; "(clean)" is added when nothing changed."""
        log.info("git_status, path: %s", path)
        out = run_git(
            path, ["status", "--short", "--branch", "--ignore-submodules=all"]
        )
        if not out.startswith("Error") and len(out.strip().splitlines()) <= 1:
            out = out.rstrip() + "\n(clean: no changes)"
        return out


class GitDiffTool(Tool):
    """``git diff``: unstaged, staged, or against a ref."""

    read_only = True

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "git_diff",
                "description": (
                    "Show changes as a diff (`git diff`). By default the unstaged "
                    "changes; `staged` for the staged ones; `ref` to compare the working "
                    "tree with a commit or branch, or a range like 'main...HEAD'. Read-only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": _PATH_PROP,
                        "staged": {
                            "type": "boolean",
                            "description": "Diff the staged changes (--cached)",
                        },
                        "ref": {
                            "type": "string",
                            "description": "Commit, branch or range to diff against",
                        },
                        "file": _FILE_PROP,
                        "stat": {
                            "type": "boolean",
                            "description": "Only a per-file summary (--stat)",
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # tool parameters
        path: str = ".",
        staged: bool = False,
        ref: str | None = None,
        file: str | None = None,
        stat: bool = False,
    ) -> str:
        """The diff text, or "(no differences)"."""
        log.info(
            "git_diff, path: %s, staged: %s, ref: %s, file: %s", path, staged, ref, file
        )
        if error := _check_ref(ref):
            return error
        args = ["diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all"]
        if staged:
            args.append("--cached")
        if stat:
            args.append("--stat")
        if ref:
            args.append(ref)
        args.append("--")
        if file:
            args.append(file)
        out = run_git(path, args)
        return out if out.strip() else "(no differences)"


class GitLogTool(Tool):
    """``git log``: recent commits, one per line or with patches."""

    read_only = True

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "git_log",
                "description": (
                    "Show commit history (`git log`): hash, date, author and subject, "
                    "newest first. Use `patch` to include each commit's diff (e.g. with "
                    "max_count 1 and a ref to show one commit). Read-only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": _PATH_PROP,
                        "max_count": {
                            "type": "integer",
                            "description": f"Number of commits (default 20, max {MAX_LOG_COUNT})",
                        },
                        "ref": {
                            "type": "string",
                            "description": "Commit, branch or range (default: HEAD)",
                        },
                        "file": _FILE_PROP,
                        "patch": {
                            "type": "boolean",
                            "description": "Include each commit's full message and diff",
                        },
                        "stat": {
                            "type": "boolean",
                            "description": "Include each commit's per-file change summary (--stat)",
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # tool parameters
        path: str = ".",
        max_count: int = 20,
        ref: str | None = None,
        file: str | None = None,
        patch: bool = False,
        stat: bool = False,
    ) -> str:
        """The log text, or "(no commits)"."""
        log.info("git_log, path: %s, ref: %s, file: %s", path, ref, file)
        if error := _check_ref(ref):
            return error
        try:
            count = max(1, min(int(max_count or 20), MAX_LOG_COUNT))
        except (TypeError, ValueError):
            count = 20
        args = ["log", "--no-ext-diff", "--no-textconv", f"--max-count={count}"]
        if patch:
            args += ["--patch", "--date=iso"]
        else:
            args += ["--date=short", "--format=%h %ad %an  %s%d"]
        if stat:
            args.append("--stat")
        if ref:
            args.append(ref)
        args.append("--")
        if file:
            args.append(file)
        out = run_git(path, args)
        return out if out.strip() else "(no commits)"
