import os

import pytest

from app.core.tool_calls import helper_tool_specs, is_read_only, run_tool
from app.tools import fs_search
from app.tools.fs_search import GlobTool, GrepTool, ListDirTool, glob_to_regex

list_dir = ListDirTool.call
glob = GlobTool.call
grep = GrepTool.call


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("import os\nprint('hello')\n")
    (tmp_path / "src" / "pkg" / "util.py").write_text("def Hello():\n    pass\n")
    (tmp_path / "README.md").write_text("Hello world\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("hello\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("hello\n")
    (tmp_path / "blob.bin").write_bytes(b"hello\0\1\2")
    return tmp_path


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("*.py", "a.py", True),
        ("*.py", "a/b.py", False),
        ("**/*.py", "a.py", True),
        ("**/*.py", "a/b/c.py", True),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/x/a.py", False),
        ("test_?.py", "test_1.py", True),
        ("[ab].txt", "a.txt", True),
        ("[!ab].txt", "a.txt", False),
        ("[!ab].txt", "c.txt", True),
        ("a.b", "axb", False),
    ],
)
def test_glob_to_regex(pattern, path, expected):
    assert (glob_to_regex(pattern).match(path) is not None) is expected


def test_list_dir_dirs_first_with_sizes(tree):
    out = list_dir(str(tree))
    lines = out.splitlines()
    assert "dirs" in lines[0]
    names = [line.split()[0] for line in lines[1:]]
    assert names.index("src/") < names.index("README.md")
    assert any(line.startswith("README.md  12B") for line in lines)


def test_list_dir_hidden_and_errors(tree):
    assert ".git/" in list_dir(str(tree))
    assert ".git/" not in list_dir(str(tree), show_hidden=False)
    assert list_dir(str(tree / "nope")).startswith("Error:")
    assert list_dir(str(tree / "README.md")).startswith("Error:")


def test_glob_basename_pattern_any_depth_skips_heavy_dirs(tree):
    assert glob("*.py", str(tree)).splitlines() == ["src/main.py", "src/pkg/util.py"]


def test_glob_path_pattern(tree):
    assert glob("src/*.py", str(tree)) == "src/main.py"
    assert glob("src/**/*.py", str(tree)).splitlines() == [
        "src/main.py",
        "src/pkg/util.py",
    ]
    assert glob("*.rs", str(tree)).startswith("No files")


def test_grep_lines_and_skips(tree):
    out = grep("hello", str(tree))
    assert out.splitlines() == ["src/main.py:2: print('hello')"]


def test_grep_options(tree):
    out = grep("hello", str(tree), ignore_case=True)
    assert "README.md:1: Hello world" in out
    assert "src/pkg/util.py:1: def Hello():" in out
    assert grep("hello", str(tree), ignore_case=True, glob="*.py").count("\n") == 1
    assert grep("hello", str(tree), ignore_case=True, files_only=True).splitlines() == [
        "README.md",
        "src/main.py",
        "src/pkg/util.py",
    ]


def test_grep_single_file_limit_and_errors(tree):
    f = tree / "many.txt"
    f.write_text("x\n" * 50)
    out = grep("x", str(f), max_results=5)
    assert out.count(": x") == 5
    assert "stopped at 5" in out
    assert grep("(", str(tree)).startswith("Error: invalid regular expression")
    assert grep("x", str(tree / "nope")).startswith("Error:")
    assert grep("zzz", str(tree)).startswith("No matches")


@pytest.mark.asyncio
async def test_registered_read_only_and_in_helper(tree):
    for name in ("list_dir", "glob", "grep", "read_file"):
        assert is_read_only(name)
    assert not is_read_only("bash")
    assert not is_read_only("write_file")
    assert not is_read_only("some_server__tool")
    helper = {s["function"]["name"] for s in helper_tool_specs}
    assert {"list_dir", "glob", "grep"} <= helper
    out = await run_tool("glob", {"pattern": "*.md", "path": str(tree)})
    assert out == "README.md"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_grep_skips_fifos(tree):
    os.mkfifo(tree / "pipe")  # opening it with no writer would block forever
    assert grep("hello", str(tree)) == "src/main.py:2: print('hello')"


def test_invalid_glob_is_an_error_text(tree):
    assert glob("[z-a].txt", str(tree)).startswith("Error: invalid glob pattern")
    out = grep("x", str(tree), glob="[z-a].txt")
    assert out.startswith("Error: invalid glob pattern")


def test_walk_stops_at_the_time_budget(tree, monkeypatch):
    monkeypatch.setattr(fs_search, "WALK_TIME_BUDGET_SECS", -1.0)
    assert "stopped after" in glob("*.py", str(tree))
    assert "stopped after" in grep("hello", str(tree))
