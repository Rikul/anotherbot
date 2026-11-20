import subprocess

import pytest

from app.core.tool_calls import helper_tool_specs, is_read_only
from app.tools.git_tools import GitDiffTool, GitLogTool, GitStatusTool

git_status = GitStatusTool.call
git_diff = GitDiffTool.call
git_log = GitLogTool.call


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.txt").write_text("one\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first commit")
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    _git(tmp_path, "commit", "-q", "-am", "second commit")
    return tmp_path


def test_status_clean_and_dirty(repo):
    out = git_status(str(repo))
    assert "## main" in out and "(clean" in out
    (repo / "a.txt").write_text("changed\n")
    (repo / "new.txt").write_text("x\n")
    out = git_status(str(repo))
    assert " M a.txt" in out and "?? new.txt" in out and "clean" not in out


def test_status_not_a_repo(tmp_path):
    assert git_status(str(tmp_path)).startswith("Error (git exit")
    assert git_status(str(tmp_path / "nope")).startswith("Error:")


def test_status_does_not_touch_index(repo):
    index = repo / ".git" / "index"
    before = index.stat().st_mtime_ns
    (repo / "a.txt").write_text("one\ntwo\n")  # same content, new mtime
    git_status(str(repo))
    assert index.stat().st_mtime_ns == before


def test_diff_unstaged_staged_ref_and_stat(repo):
    assert git_diff(str(repo)) == "(no differences)"
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    assert "+three" in git_diff(str(repo))
    assert git_diff(str(repo), staged=True) == "(no differences)"
    _git(repo, "add", "a.txt")
    assert "+three" in git_diff(str(repo), staged=True)
    assert "+two" in git_diff(str(repo), ref="HEAD~1")
    assert "a.txt" in git_diff(str(repo), ref="HEAD~1", stat=True)
    assert git_diff(str(repo), ref="HEAD~1", file="other.txt") == "(no differences)"


@pytest.mark.parametrize("tool", [git_diff, git_log])
def test_option_like_ref_is_refused(repo, tool):
    target = repo / "written.txt"
    out = tool(str(repo), ref=f"--output={target}")
    assert out.startswith("Error: invalid ref")
    assert not target.exists()


def test_log_oneline_count_and_patch(repo):
    lines = git_log(str(repo)).splitlines()
    assert len(lines) == 2
    assert "Tester" in lines[0] and "second commit" in lines[0]
    assert len(git_log(str(repo), max_count=1).splitlines()) == 1
    out = git_log(str(repo), max_count=1, ref="HEAD~1", patch=True)
    assert "first commit" in out and "+one" in out and "second commit" not in out
    assert "first commit" in git_log(str(repo), file="a.txt")


def test_registered_read_only_and_in_helper():
    helper = {s["function"]["name"] for s in helper_tool_specs}
    for name in ("git_status", "git_diff", "git_log"):
        assert is_read_only(name)
        assert name in helper


def test_configured_filters_never_run(repo):
    """A clean/process filter runs when git compares the work tree with the
    index; the read-only tools must not run repository-configured programs."""
    marker = repo / "filter-ran"
    (repo / ".gitattributes").write_text("*.txt filter=evil\n")
    _git(repo, "config", "filter.evil.clean", f"touch {marker}; cat")
    _git(repo, "config", "filter.evil.process", f"touch {marker}")
    _git(repo, "config", "filter.evil.required", "true")
    (repo / "a.txt").write_text("changed\n")
    assert "+changed" in git_diff(str(repo))
    assert " M a.txt" in git_status(str(repo))
    assert "second commit" in git_log(str(repo), patch=True)
    assert not marker.exists()
