from unittest.mock import patch, MagicMock

from app.tools.bash import BashTool

bash = BashTool.call


def test_bash_runs_simple_command():
    with patch("app.tools.bash.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)
        result = bash("echo hello")
    assert "hello" in result


def test_bash_returns_stderr_on_failure():
    with patch("app.tools.bash.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="", stderr="stderr: No such file or directory\n", returncode=1
        )
        result = bash("ls /nonexistent_path_xyz")
    assert result  # should contain some output (error in stderr)
    assert "No such file" in result or "cannot access" in result or "stderr" in result


def test_bash_captures_stdout():
    with patch("app.tools.bash.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="abc", stderr="", returncode=0)
        result = bash("printf 'abc'")
    assert "abc" in result


def test_bash_timeout_error():
    with patch("app.tools.bash.subprocess.run", side_effect=Exception("timed out")):
        result = bash("sleep 100")
    assert "Error" in result


def test_bash_multiline_output():
    with patch("app.tools.bash.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="a\nb\nc\n", stderr="", returncode=0)
        result = bash("printf 'a\nb\nc'")
    assert "a" in result
    assert "b" in result
    assert "c" in result


def test_bash_returns_combined_stderr():
    with patch("app.tools.bash.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="out\n", stderr="err\n", returncode=0)
        result = bash("echo out && echo err >&2")
    assert "out" in result
    assert "err" in result
