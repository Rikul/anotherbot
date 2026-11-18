"""Self-update: fetch and pull new code for the bot's own git checkout.

``restart.sh`` (and the ``/restart`` command) reload code from disk; this
module supplies the other half — getting new commits from the remote — with
guards so a botched update cannot destroy local work.

Nothing here raises. Every entry point returns a report dataclass that the
caller formats for a chat channel, a tool result or the log. Mutating steps
are ordered so the working tree is never left half-updated without the report
saying so: a backup branch is created before HEAD moves, a dirty tree is
refused unless explicitly allowed, and a conflict is reported rather than
resolved.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .app_logging import log

# Repo root (holds pyproject.toml), same convention as infra.restart.
REPO_DIR = Path(__file__).resolve().parents[2]

GIT_TIMEOUT = 60
FETCH_TIMEOUT = 120

# How HEAD is moved onto the upstream after fetching.
STRATEGIES = ("ff-only", "rebase", "merge")


@dataclass
class _Cmd:  # pylint: disable=too-few-public-methods  # plain command result
    """Outcome of one git invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether git exited zero."""
        return self.returncode == 0

    @property
    def out(self) -> str:
        """stdout with surrounding whitespace removed."""
        return self.stdout.strip()

    @property
    def err(self) -> str:
        """stderr with surrounding whitespace removed."""
        return self.stderr.strip()


@dataclass
class RepoStatus:  # pylint: disable=too-few-public-methods  # plain data holder
    """Where the checkout stands right now."""

    repo: str
    ok: bool = False
    branch: str = ""
    head: str = ""
    head_subject: str = ""
    upstream: str = ""
    dirty: bool = False
    dirty_files: int = 0
    detached: bool = False
    error: str = ""


@dataclass
class UpdateReport:  # pylint: disable=too-few-public-methods  # plain data holder
    """Result of a check or update, ready to be formatted."""

    action: str
    status: RepoStatus
    ahead: int = 0
    behind: int = 0
    incoming: list[tuple[str, str]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    strategy: str = ""
    backup_branch: str = ""
    before: str = ""
    after: str = ""
    updated: bool = False
    restarted: bool = False
    conflicted: bool = False
    message: str = ""
    error: str = ""


def _resolve(repo: Path | str | None) -> Path:
    """Return ``repo`` as a Path, falling back to the running checkout."""
    return Path(repo) if repo is not None else Path(REPO_DIR)


def _git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT) -> _Cmd:
    """Run git in ``repo``; never raises, failures come back as a result."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        return _Cmd(127, "", "git executable not found")
    except subprocess.TimeoutExpired:
        return _Cmd(124, "", f"git {' '.join(args)} timed out after {timeout}s")
    except OSError as e:  # pylint: disable=broad-exception-caught  # reported, not raised
        return _Cmd(1, "", str(e))
    return _Cmd(result.returncode, result.stdout, result.stderr)


def repo_status(repo: Path | str | None = None) -> RepoStatus:
    """Inspect ``repo``: branch, HEAD, upstream, and whether the tree is dirty.

    Args:
        repo: path to a git checkout (or any directory inside one); defaults to
            the checkout this process is running from.

    Returns:
        A ``RepoStatus``; ``ok`` is False and ``error`` set if ``repo`` is not
        a usable git working tree.
    """
    path = _resolve(repo)
    status = RepoStatus(repo=str(path))

    if not path.is_dir():
        status.error = f"not a directory: {path}"
        return status

    if _git(path, "rev-parse", "--is-inside-work-tree").out != "true":
        status.error = f"not a git working tree: {path}"
        return status

    top = _git(path, "rev-parse", "--show-toplevel")
    if top.ok and top.out:
        status.repo = top.out

    branch = _git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.ok and branch.out:
        status.branch = branch.out
    else:
        status.detached = True

    head = _git(path, "rev-parse", "--short", "HEAD")
    if head.ok:
        status.head = head.out

    subject = _git(path, "log", "-1", "--pretty=%s")
    if subject.ok:
        status.head_subject = subject.out

    upstream = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.ok and upstream.out and upstream.out != "@{u}":
        status.upstream = upstream.out

    porcelain = _git(path, "status", "--porcelain")
    if porcelain.ok:
        entries = [line for line in porcelain.out.splitlines() if line.strip()]
        status.dirty_files = len(entries)
        status.dirty = bool(entries)

    status.ok = True
    return status


def fetch(repo: Path | str | None = None, remote: str = "") -> _Cmd:
    """Fetch and prune from ``remote`` (the upstream's remote when empty)."""
    args = ["fetch", "--prune", "--quiet"]
    if remote:
        args.append(remote)
    return _git(_resolve(repo), *args, timeout=FETCH_TIMEOUT)


def _ahead_behind(repo: Path) -> tuple[int, int] | None:
    """Return ``(ahead, behind)`` relative to the upstream, or None if unknown."""
    result = _git(repo, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if not result.ok:
        return None
    parts = result.out.split()
    if len(parts) != 2:
        return None
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return ahead, behind


def _incoming(repo: Path, limit: int = 20) -> list[tuple[str, str]]:
    """Commits on the upstream that HEAD does not have, newest first."""
    result = _git(repo, "log", "--pretty=%h\t%s", f"-n{limit}", "HEAD..@{u}")
    if not result.ok:
        return []
    commits = []
    for line in result.out.splitlines():
        if "\t" in line:
            sha, subject = line.split("\t", 1)
            commits.append((sha.strip(), subject.strip()))
        elif line.strip():
            commits.append((line.strip(), ""))
    return commits


def _changed_files(repo: Path, old: str, new: str) -> list[str]:
    """Paths that differ between two revisions."""
    result = _git(repo, "diff", "--name-only", f"{old}..{new}")
    if not result.ok:
        return []
    return [line.strip() for line in result.out.splitlines() if line.strip()]


def _unmerged(repo: Path) -> list[str]:
    """Paths left with conflict markers by a failed merge or rebase."""
    result = _git(repo, "diff", "--name-only", "--diff-filter=U")
    if not result.ok:
        return []
    return [line.strip() for line in result.out.splitlines() if line.strip()]


def _make_backup_branch(repo: Path, head: str) -> str:
    """Point a ``backup-pre-update-<timestamp>`` branch at ``head``; return its name."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"backup-pre-update-{stamp}"
    for suffix in range(2, 100):
        if not _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").ok:
            break
        name = f"backup-pre-update-{stamp}-{suffix}"
    if _git(repo, "branch", name, head).ok:
        return name
    return ""


def _blocked(report: UpdateReport, reason: str) -> UpdateReport:
    """Mark ``report`` as failed with ``reason`` and return it."""
    report.error = reason
    report.message = reason
    log.warning("self-update: %s", reason)
    return report


def check(repo: Path | str | None = None, *, do_fetch: bool = True) -> UpdateReport:
    """Report what an update would do, without changing the checkout.

    Args:
        repo: git checkout to inspect; defaults to this process's checkout.
        do_fetch: fetch from the remote first, so the answer is current.

    Returns:
        An ``UpdateReport`` with ``behind``, ``incoming`` commits and the
        ``changed_files`` the pull would bring in.
    """
    status = repo_status(repo)
    report = UpdateReport(action="check", status=status)

    if not status.ok:
        return _blocked(report, status.error)
    if status.detached:
        return _blocked(report, "HEAD is detached")
    if not status.upstream:
        return _blocked(report, "no upstream branch configured")

    root = Path(status.repo)

    if do_fetch:
        fetched = fetch(root)
        if not fetched.ok:
            return _blocked(report, f"git fetch failed: {fetched.err or fetched.out}")

    counts = _ahead_behind(root)
    if counts is None:
        return _blocked(report, "cannot compare HEAD with its upstream")

    report.ahead, report.behind = counts
    if report.behind:
        report.incoming = _incoming(root)
        report.changed_files = _changed_files(root, status.head, "@{u}")

    if not report.behind:
        report.message = "Already up to date."
    else:
        report.message = (
            f"{report.behind} new commit(s) available "
            f"({len(report.changed_files)} file(s) changed)."
        )
    return report


def update(  # pylint: disable=too-many-arguments,too-many-branches,too-many-statements
    repo: Path | str | None = None,
    *,
    strategy: str = "ff-only",
    allow_dirty: bool = False,
    stash: bool = False,
    backup: bool = True,
    restart_after: bool = True,
) -> UpdateReport:
    """Pull the upstream into the checkout, then optionally restart the bot.

    Args:
        repo: git checkout to update; defaults to this process's checkout.
        strategy: how to move HEAD onto the upstream — ``ff-only`` (refuse a
            diverged branch), ``rebase`` (replay local commits on top) or
            ``merge`` (create a merge commit).
        allow_dirty: proceed with uncommitted changes present. Off by default:
            a pull over local edits can fail halfway and is easy to regret.
        stash: stash local changes (including untracked) before pulling,
            leaving them in the stash afterwards.
        backup: create a ``backup-pre-update-<timestamp>`` branch at the old
            HEAD before moving it.
        restart_after: request a restart so the new code is loaded. Only
            happens when the checkout actually moved.

    Returns:
        An ``UpdateReport``; check ``updated`` for success and ``error`` for a
        reason when the update was refused or failed.
    """
    if strategy not in STRATEGIES:
        return _blocked(
            UpdateReport(action="update", status=RepoStatus(repo=str(_resolve(repo)))),
            f"unknown strategy {strategy!r} (expected one of {', '.join(STRATEGIES)})",
        )

    status = repo_status(repo)
    report = UpdateReport(action="update", status=status, strategy=strategy)

    if not status.ok:
        return _blocked(report, status.error)
    if status.detached:
        return _blocked(report, "HEAD is detached")
    if not status.upstream:
        return _blocked(report, "no upstream branch configured")

    root = Path(status.repo)
    report.before = status.head
    prefix = ""

    if status.dirty:
        if stash:
            stashed = _git(
                root, "stash", "push", "--include-untracked", "-m", "self-update"
            )
            if not stashed.ok:
                return _blocked(
                    report, f"git stash failed: {stashed.err or stashed.out}"
                )
            prefix = "Stashed local changes. "
        elif not allow_dirty:
            return _blocked(
                report,
                f"{status.dirty_files} uncommitted change(s) in the working tree; "
                "commit them, or use stash / allow_dirty",
            )

    fetched = fetch(root)
    if not fetched.ok:
        return _blocked(report, f"git fetch failed: {fetched.err or fetched.out}")

    counts = _ahead_behind(root)
    if counts is None:
        return _blocked(report, "cannot compare HEAD with its upstream")
    report.ahead, report.behind = counts

    if not report.behind:
        report.message = (prefix + "Already up to date.").strip()
        return report

    report.incoming = _incoming(root)
    report.changed_files = _changed_files(root, status.head, "@{u}")

    if report.ahead and strategy == "ff-only":
        return _blocked(
            report,
            f"branch has diverged ({report.ahead} local commit(s) not on the "
            "upstream); use strategy=rebase or merge",
        )

    if backup:
        report.backup_branch = _make_backup_branch(root, status.head)

    if strategy == "ff-only":
        integrated = _git(root, "merge", "--ff-only", "@{u}")
    elif strategy == "rebase":
        integrated = _git(root, "rebase", "@{u}", timeout=FETCH_TIMEOUT)
    else:
        integrated = _git(root, "merge", "--no-edit", "@{u}")

    if not integrated.ok:
        report.conflicted = bool(_unmerged(root)) or any(
            (root / ".git" / marker).exists()
            for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD")
        )
        detail = integrated.err or integrated.out
        report.error = f"git {strategy} failed: {detail}"
        if report.conflicted:
            report.message = "merge/rebase conflict; resolve it and finish manually"
        log.warning("self-update failed (%s): %s", strategy, detail)
        return report

    after = repo_status(root)
    report.after = after.head
    report.updated = report.after != report.before
    report.status = after

    if not report.updated:
        report.message = (prefix + "Already up to date.").strip()
        return report

    summary = f"Updated {report.before} → {report.after} ({report.behind} commit(s))."
    if report.backup_branch:
        summary += f" Backup branch: {report.backup_branch}."
    report.message = (prefix + summary).strip()
    log.info("self-update: %s", summary)

    if restart_after:
        from . import restart  # local import: keeps this module import-light

        restart.request_restart()
        report.restarted = True
        report.message += " Restarting to load the new code."

    return report


def format_report(report: UpdateReport) -> str:
    """Render a report as plain text for a chat reply or tool result."""
    status = report.status
    where = f"{status.branch or 'detached'} @ {status.head or '?'}" if status.ok else ""
    lines = [f"{status.repo}" + (f" ({where})" if where else "")]

    if report.error:
        lines.append(report.error)
        if report.conflicted and report.message:
            lines.append(report.message)
        return "\n".join(lines)

    lines.append(report.message)

    if report.action == "check":
        for sha, subject in report.incoming:
            lines.append(f"  {sha} {subject}".rstrip())

    if report.changed_files:
        shown = report.changed_files[:10]
        lines.append(f"Files changed: {len(report.changed_files)}")
        lines.extend(f"  {path}" for path in shown)
        if len(report.changed_files) > len(shown):
            lines.append(f"  ... and {len(report.changed_files) - len(shown)} more")

    if report.ahead and not report.updated:
        lines.append(f"Local commits not on the upstream: {report.ahead}")

    return "\n".join(lines)
