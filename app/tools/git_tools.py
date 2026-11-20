"""Read-only git tools: ``git_status``, ``git_diff`` and ``git_log``.

They run git directly (no shell) with options that keep it from changing
anything or running repository-configured programs: no optional index locks,
no fsmonitor hook, no external diff or textconv drivers, no pager or prompts.
"""

import os
import subprocess

from ..core.tool import Tool
from ..infra.app_logging import log

GIT_TIMEOUT_SECS = 30
MAX_LOG_COUNT = 200

_GIT_ENV = {
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",  # status must not rewrite .git/index
}


def run_git(path: str, args: list[str]) -> str:
    """Run ``git -C path <args>`` and return stdout (stderr appended); errors as text."""
    cwd = os.path.expanduser(path or ".")
    if not os.path.isdir(cwd):
        return f"Error: directory {cwd} does not exist"
    cmd = [
        "git",
        "-C",
        cwd,
        "--no-pager",
        "-c",
        "core.fsmonitor=false",  # a configured fsmonitor runs a program
        "-c",
        "color.ui=false",
        *args,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECS,
            env={**os.environ, **_GIT_ENV},
            check=False,
        )
    except FileNotFoundError:
        return "Error: git is not installed"
    except subprocess.TimeoutExpired:
        return f"Error: git timed out after {GIT_TIMEOUT_SECS} seconds"
    except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
        return f"Error running git: {e}"
    out = proc.stdout.decode(errors="replace")
    err = proc.stderr.decode(errors="replace").strip()
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
        out = run_git(path, ["status", "--short", "--branch"])
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
        args = ["diff", "--no-ext-diff", "--no-textconv"]
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
        if ref:
            args.append(ref)
        args.append("--")
        if file:
            args.append(file)
        out = run_git(path, args)
        return out if out.strip() else "(no commits)"
