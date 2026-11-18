"""Tests for ``app.infra.self_update`` and its tool/command wrappers.

Each test builds a real origin + clone in ``tmp_path`` and drives the module
with real git. Nothing here touches the checkout the tests run from.
"""

import subprocess
from types import SimpleNamespace

import pytest

from app.infra import self_update


def _git(cwd, *args):
    """Run git, raising on failure, and return stdout."""
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout


def _configure(repo):
    """Give a repo a committer identity so commits work in any environment."""
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit(repo, filename, content, message):
    """Write ``filename``, stage it and commit."""
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "--short", "HEAD").strip()


def _clone(origin, path):
    """Clone ``origin`` to ``path`` with a local identity."""
    _git(path.parent, "clone", "--quiet", str(origin), str(path))
    return _configure(path)


@pytest.fixture
def repos(tmp_path):
    """A bare origin, a clone ``work`` and a second clone ``other``.

    ``work`` is where updates are applied; commits pushed from ``other`` stand
    in for upstream releases.
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--quiet", "-b", "main")

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "--quiet", "-b", "main")
    _configure(work)
    _commit(work, "a.txt", "one\n", "initial commit")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "--quiet", "-u", "origin", "main")

    other = _clone(origin, tmp_path / "other")
    return SimpleNamespace(origin=origin, work=work, other=other, tmp=tmp_path)


# --------------------------------------------------------------------------- #
# repo_status
# --------------------------------------------------------------------------- #


def test_status_reports_branch_head_and_upstream(repos):
    status = self_update.repo_status(repos.work)

    assert status.ok
    assert status.branch == "main"
    assert status.upstream == "origin/main"
    assert status.head
    assert status.head_subject == "initial commit"
    assert not status.dirty
    assert not status.detached


def test_status_reports_a_dirty_tree(repos):
    (repos.work / "a.txt").write_text("edited\n")

    status = self_update.repo_status(repos.work)

    assert status.ok
    assert status.dirty
    assert status.dirty_files == 1


def test_status_reports_untracked_files_as_dirty(repos):
    (repos.work / "new.txt").write_text("hello\n")

    status = self_update.repo_status(repos.work)

    assert status.dirty
    assert status.dirty_files == 1


def test_status_rejects_a_plain_directory(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    status = self_update.repo_status(plain)

    assert not status.ok
    assert "not a git working tree" in status.error


def test_status_rejects_a_missing_path(tmp_path):
    status = self_update.repo_status(tmp_path / "nope")

    assert not status.ok
    assert "not a directory" in status.error


def test_status_detects_a_detached_head(repos):
    head = _git(repos.work, "rev-parse", "HEAD").strip()
    _git(repos.work, "checkout", "--quiet", head)

    status = self_update.repo_status(repos.work)

    assert status.detached
    assert status.branch == ""


def test_status_resolves_the_work_tree_root_from_a_subdirectory(repos):
    sub = repos.work / "sub"
    sub.mkdir()

    status = self_update.repo_status(sub)

    assert status.ok
    assert status.repo == str(repos.work.resolve())


def test_status_without_upstream_leaves_it_empty(repos):
    _git(repos.work, "branch", "--unset-upstream")

    status = self_update.repo_status(repos.work)

    assert status.ok
    assert status.upstream == ""


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #


def test_check_reports_an_incoming_commit(repos):
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.check(repos.work)

    assert report.action == "check"
    assert report.error == ""
    assert report.behind == 1
    assert report.ahead == 0
    assert [subject for _, subject in report.incoming] == ["add b"]
    assert report.changed_files == ["b.txt"]
    assert "1 new commit(s) available" in report.message


def test_check_reports_already_up_to_date(repos):
    report = self_update.check(repos.work)

    assert report.behind == 0
    assert report.incoming == []
    assert report.message == "Already up to date."


def test_check_does_not_move_head(repos):
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")
    before = _git(repos.work, "rev-parse", "HEAD").strip()

    self_update.check(repos.work)

    assert _git(repos.work, "rev-parse", "HEAD").strip() == before


def test_check_reports_ahead_commits(repos):
    _commit(repos.work, "local.txt", "local\n", "local work")

    report = self_update.check(repos.work)

    assert report.ahead == 1
    assert report.behind == 0
    assert report.message == "Already up to date."


def test_check_without_fetch_uses_the_cached_refs(repos):
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.check(repos.work, do_fetch=False)

    assert report.behind == 0  # the remote-tracking ref is stale


def test_check_refuses_a_detached_head(repos):
    head = _git(repos.work, "rev-parse", "HEAD").strip()
    _git(repos.work, "checkout", "--quiet", head)

    report = self_update.check(repos.work)

    assert report.error == "HEAD is detached"


def test_check_refuses_without_upstream(repos):
    _git(repos.work, "branch", "--unset-upstream")

    report = self_update.check(repos.work)

    assert report.error == "no upstream branch configured"


def test_check_reports_an_unreachable_remote(repos):
    _git(repos.work, "remote", "set-url", "origin", str(repos.tmp / "gone.git"))

    report = self_update.check(repos.work)

    assert "git fetch failed" in report.error


# --------------------------------------------------------------------------- #
# update
# --------------------------------------------------------------------------- #


def test_update_pulls_and_advances_head(repos, monkeypatch):
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")
    requests = []
    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)
    monkeypatch.setattr(
        "app.infra.restart.request_restart", lambda: requests.append(True)
    )

    report = self_update.update(repos.work)

    assert report.updated
    assert report.before != report.after
    assert report.behind == 1
    assert (repos.work / "b.txt").exists()
    assert report.restarted
    assert requests == [True]
    assert "Restarting to load the new code." in report.message


def test_update_is_a_noop_when_up_to_date(repos, monkeypatch):
    requests = []
    monkeypatch.setattr(
        "app.infra.restart.request_restart", lambda: requests.append(True)
    )

    report = self_update.update(repos.work)

    assert not report.updated
    assert report.message == "Already up to date."
    assert not report.restarted
    assert requests == []


def test_update_skips_restart_when_asked(repos, monkeypatch):
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")
    requests = []
    monkeypatch.setattr(
        "app.infra.restart.request_restart", lambda: requests.append(True)
    )

    report = self_update.update(repos.work, restart_after=False)

    assert report.updated
    assert not report.restarted
    assert requests == []


def test_update_refuses_a_dirty_tree(repos):
    (repos.work / "a.txt").write_text("edited\n")
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work)

    assert report.error
    assert "uncommitted change(s)" in report.error
    assert not report.updated
    assert (repos.work / "a.txt").read_text() == "edited\n"  # left untouched


def test_update_stashes_a_dirty_tree_when_asked(repos):
    (repos.work / "a.txt").write_text("edited\n")
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work, stash=True)

    assert report.updated
    assert report.message.startswith("Stashed local changes.")
    assert (repos.work / "a.txt").read_text() == "one\n"  # restored by the pull
    assert "self-update" in _git(repos.work, "stash", "list")


def test_update_allows_a_dirty_tree_when_asked(repos):
    # A change git can carry across the fast-forward.
    (repos.work / "untracked.txt").write_text("scratch\n")
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work, allow_dirty=True)

    assert report.updated
    assert (repos.work / "untracked.txt").exists()


def test_ff_only_refuses_a_diverged_branch(repos):
    _commit(repos.work, "local.txt", "local\n", "local work")
    local_head = _git(repos.work, "rev-parse", "HEAD").strip()
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work, strategy="ff-only")

    assert report.error
    assert "diverged" in report.error
    assert report.ahead == 1
    assert report.behind == 1
    assert not report.updated
    assert _git(repos.work, "rev-parse", "HEAD").strip() == local_head


def test_rebase_replays_local_commits_on_top(repos):
    _commit(repos.work, "local.txt", "local\n", "local work")
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work, strategy="rebase")

    assert report.updated
    assert report.before != report.after
    subjects = _git(repos.work, "log", "--pretty=%s", "-2").splitlines()
    assert subjects == ["local work", "add b"]
    assert (repos.work / "b.txt").exists()
    assert (repos.work / "local.txt").exists()


def test_merge_creates_a_merge_commit(repos):
    _commit(repos.work, "local.txt", "local\n", "local work")
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work, strategy="merge")

    assert report.updated
    subjects = _git(repos.work, "log", "--pretty=%s", "-3").splitlines()
    assert "Merge remote-tracking branch 'origin/main'" in subjects[0]


def test_rebase_reports_a_conflict_without_leaving_a_clean_tree(repos):
    _commit(repos.work, "shared.txt", "local\n", "local edit")
    _commit(repos.other, "shared.txt", "remote\n", "remote edit")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work, strategy="rebase")

    assert report.error
    assert report.conflicted
    assert "conflict" in report.message
    assert not report.updated
    assert (repos.work / ".git" / "rebase-merge").is_dir()
    _git(repos.work, "rebase", "--abort")  # leave the fixture tidy


def test_update_creates_a_backup_branch_at_the_old_head(repos):
    before = _git(repos.work, "rev-parse", "HEAD").strip()
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work)

    assert report.backup_branch.startswith("backup-pre-update-")
    assert _git(repos.work, "rev-parse", report.backup_branch).strip() == before
    assert f"Backup branch: {report.backup_branch}" in report.message


def test_update_without_backup_creates_no_branch(repos):
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(repos.work, backup=False)

    assert report.updated
    assert report.backup_branch == ""
    assert "backup-pre-update" not in _git(repos.work, "branch", "--list")


def test_update_refuses_a_detached_head(repos):
    head = _git(repos.work, "rev-parse", "HEAD").strip()
    _git(repos.work, "checkout", "--quiet", head)

    report = self_update.update(repos.work)

    assert report.error == "HEAD is detached"
    assert not report.updated


def test_update_rejects_an_unknown_strategy(repos):
    report = self_update.update(repos.work, strategy="hard-reset")

    assert "unknown strategy" in report.error


def test_update_reports_a_missing_upstream(repos):
    _git(repos.work, "branch", "--unset-upstream")

    report = self_update.update(repos.work)

    assert report.error == "no upstream branch configured"


def test_update_reports_a_fetch_failure(repos):
    _git(repos.work, "remote", "set-url", "origin", str(repos.tmp / "gone.git"))

    report = self_update.update(repos.work)

    assert "git fetch failed" in report.error
    assert not report.updated


def test_update_defaults_to_the_running_checkout(repos, monkeypatch):
    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    report = self_update.update(restart_after=False)

    assert report.updated
    assert report.status.repo == str(repos.work.resolve())


# --------------------------------------------------------------------------- #
# format_report
# --------------------------------------------------------------------------- #


def test_format_report_for_a_check_lists_incoming_commits(repos):
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    text = self_update.format_report(self_update.check(repos.work))

    assert "main @ " in text
    assert "1 new commit(s) available (1 file(s) changed)." in text
    assert "add b" in text
    assert "  b.txt" in text


def test_format_report_for_an_error_is_short(repos):
    _git(repos.work, "branch", "--unset-upstream")

    text = self_update.format_report(self_update.update(repos.work))

    assert text.splitlines()[1] == "no upstream branch configured"


def test_format_report_for_an_update_shows_backup_and_files(repos):
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    text = self_update.format_report(
        self_update.update(repos.work, restart_after=False)
    )

    assert "Updated " in text
    assert "Backup branch: backup-pre-update-" in text
    assert "Files changed: 1" in text


def test_format_report_truncates_a_long_file_list(repos):
    for i in range(12):
        _commit(repos.other, f"f{i}.txt", "x\n", f"add f{i}")
    _git(repos.other, "push", "--quiet")

    text = self_update.format_report(self_update.check(repos.work))

    assert "Files changed: 12" in text
    assert "... and 2 more" in text


# --------------------------------------------------------------------------- #
# tool wrapper
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_check_reports_without_changing_anything(repos, monkeypatch):
    from app.tools.self_update import SelfUpdateTool

    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")
    before = _git(repos.work, "rev-parse", "HEAD").strip()

    out = await SelfUpdateTool.call(action="check")

    assert "1 new commit(s) available" in out
    assert _git(repos.work, "rev-parse", "HEAD").strip() == before


@pytest.mark.asyncio
async def test_tool_update_pulls(repos, monkeypatch):
    from app.tools.self_update import SelfUpdateTool

    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)
    monkeypatch.setattr("app.infra.restart.request_restart", lambda: None)
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")

    out = await SelfUpdateTool.call(action="update", restart=False)

    assert "Updated " in out
    assert (repos.work / "b.txt").exists()


@pytest.mark.asyncio
async def test_tool_rejects_an_unknown_action(repos, monkeypatch):
    from app.tools.self_update import SelfUpdateTool

    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)

    out = await SelfUpdateTool.call(action="nuke")

    assert "Unknown action" in out


def test_tool_is_registered_and_not_available_to_helpers():
    from app.core import tool_calls

    assert "self_update" in tool_calls.tool_registry
    assert "self_update" not in tool_calls._HELPER_AGENT_TOOLS
    names = [spec["function"]["name"] for spec in tool_calls.all_tool_specs]
    assert "self_update" in names


def test_tool_spec_declares_every_strategy():
    from app.tools.self_update import SelfUpdateTool

    params = SelfUpdateTool.spec()["function"]["parameters"]["properties"]

    assert set(params["strategy"]["enum"]) == set(self_update.STRATEGIES)
    assert params["action"]["enum"] == ["check", "update"]
    assert SelfUpdateTool.spec()["function"]["parameters"]["required"] == ["action"]


# --------------------------------------------------------------------------- #
# /update command
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_update_command_pulls_and_restarts(repos, monkeypatch):
    from app.core.background_agent import BackgroundAgent

    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)
    requests = []
    monkeypatch.setattr(
        "app.infra.restart.request_restart", lambda: requests.append(True)
    )
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")
    agent = BackgroundAgent.__new__(BackgroundAgent)

    reply = await BackgroundAgent._update_cmd(agent, "")

    assert "Updated " in reply
    assert requests == [True]


@pytest.mark.asyncio
async def test_update_command_check_is_a_dry_run(repos, monkeypatch):
    from app.core.background_agent import BackgroundAgent

    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)
    requests = []
    monkeypatch.setattr(
        "app.infra.restart.request_restart", lambda: requests.append(True)
    )
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")
    before = _git(repos.work, "rev-parse", "HEAD").strip()
    agent = BackgroundAgent.__new__(BackgroundAgent)

    reply = await BackgroundAgent._update_cmd(agent, "check")

    assert "1 new commit(s) available" in reply
    assert _git(repos.work, "rev-parse", "HEAD").strip() == before
    assert requests == []


@pytest.mark.asyncio
async def test_update_command_honours_the_strategy_argument(repos, monkeypatch):
    from app.core.background_agent import BackgroundAgent

    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)
    monkeypatch.setattr("app.infra.restart.request_restart", lambda: None)
    _commit(repos.work, "local.txt", "local\n", "local work")
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")
    agent = BackgroundAgent.__new__(BackgroundAgent)

    reply = await BackgroundAgent._update_cmd(agent, "rebase")

    assert "Updated " in reply
    assert "local work" in _git(repos.work, "log", "--pretty=%s", "-2")


@pytest.mark.asyncio
async def test_update_command_force_allows_a_dirty_tree(repos, monkeypatch):
    from app.core.background_agent import BackgroundAgent

    monkeypatch.setattr(self_update, "REPO_DIR", repos.work)
    monkeypatch.setattr("app.infra.restart.request_restart", lambda: None)
    (repos.work / "untracked.txt").write_text("scratch\n")
    _commit(repos.other, "b.txt", "two\n", "add b")
    _git(repos.other, "push", "--quiet")
    agent = BackgroundAgent.__new__(BackgroundAgent)

    reply = await BackgroundAgent._update_cmd(agent, "force")

    assert "Updated " in reply
