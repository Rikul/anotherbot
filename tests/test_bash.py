from unittest.mock import MagicMock, patch

from app.tools.bash import BashTool, _summarize_command_for_logs
bash = BashTool.call


def test_bash_runs_simple_command():
    with patch("app.tools.bash.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)
        result = bash("echo hello")
    assert "hello" in result


def test_bash_returns_stderr_on_failure():
    with patch("app.tools.bash.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="stderr: No such file or directory\n", returncode=1)
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


def test_summarize_command_for_logs_redacts_obvious_secrets():
    url_with_credentials = "'https://" + "user" + ":" + "userpass" + "@example.com'"
    command = " ".join((
        "API_KEY=supersecret",
        "curl",
        "-H",
        "'Authorization: bearer_value'",
        "'https://example.com?token=xyz'",
        url_with_credentials,
    ))
    summary = _summarize_command_for_logs(command)
    assert "API_KEY=[REDACTED]" in summary
    assert "Authorization: [REDACTED]" in summary
    assert "?token=[REDACTED]" in summary
    assert "https://[REDACTED]@example.com" in summary


def test_summarize_command_for_logs_truncates_long_commands():
    summary = _summarize_command_for_logs("x" * 250, max_length=40)
    assert summary.endswith("...")
    assert len(summary) == 40


def test_bash_logs_redacted_command_on_error():
    command = "TOKEN=topsecret echo hello"
    with patch("app.tools.bash.log.info") as info_log, \
         patch("app.tools.bash.log.error") as error_log, \
         patch("app.tools.bash.subprocess.run", side_effect=Exception("timed out")):
        bash(command)

    logged_command = info_log.call_args.args[0]
    logged_error = error_log.call_args.args[0]
    assert "topsecret" not in logged_command
    assert "topsecret" not in logged_error
    assert "[REDACTED]" in logged_command
    assert "[REDACTED]" in logged_error
