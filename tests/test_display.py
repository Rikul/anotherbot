import logging
from unittest.mock import patch

from app.cli.cli import ask_permission
from app.infra.app_logging import log


def test_log_is_configured():
    """Test that log is a properly configured logger instance"""
    assert isinstance(log, logging.Logger)
    assert log.name == "app.infra.app_logging"


def test_log_level_is_info_by_default():
    """Test that log level is INFO by default"""
    assert log.level <= logging.INFO


def test_library_loggers_silenced():
    """Test that noisy library loggers are set to WARNING level"""
    for name in ("httpx", "httpcore", "LiteLLM", "litellm", "urllib3"):
        assert logging.getLogger(name).level == logging.WARNING


def test_ask_permission_returns_true_when_confirmed():
    """Test ask_permission returns True when user confirms"""
    with patch("builtins.input", return_value="y"):
        result = ask_permission("bash", {"command": "echo hi"})
    assert result is True


def test_ask_permission_returns_true_when_empty_input():
    """Test ask_permission returns True when user presses Enter"""
    with patch("builtins.input", return_value=""):
        result = ask_permission("bash", {"command": "echo hi"})
    assert result is True


def test_ask_permission_returns_false_when_denied():
    """Test ask_permission returns False when user denies"""
    with patch("builtins.input", return_value="n"):
        result = ask_permission("read_file", {"file_path": "/tmp/x"})
    assert result is False


def test_ask_permission_prints_tool_name_and_args(capsys):
    """Test ask_permission prints tool name and args"""
    with patch("builtins.input", return_value="y"):
        ask_permission("write_file", {"file_path": "/tmp/out.txt", "content": "hello"})
    
    captured = capsys.readouterr()
    assert "write_file" in captured.out
    assert "/tmp/out.txt" in captured.out