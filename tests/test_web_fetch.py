from unittest.mock import ANY, MagicMock, patch

from app.tools.web_fetch import WebFetchTool
web_fetch = WebFetchTool.call


def _make_completed_process(stdout="", returncode=0, stderr=""):
    mock = MagicMock()
    mock.stdout = stdout
    mock.returncode = returncode
    mock.stderr = stderr
    return mock


def test_web_fetch_returns_page_content():
    mock_result = _make_completed_process(stdout="<html>Hello</html>")
    with patch("app.tools.web_fetch.subprocess.run", return_value=mock_result):
        result = web_fetch("http://example.com")
    assert "Hello" in result


def test_web_fetch_returns_error_on_nonzero_returncode():
    mock_result = _make_completed_process(returncode=1, stderr="connection refused")
    with patch("app.tools.web_fetch.subprocess.run", return_value=mock_result):
        result = web_fetch("http://bad-url")
    assert "Error" in result


def test_web_fetch_returns_error_on_exception():
    with patch("app.tools.web_fetch.subprocess.run", side_effect=Exception("timeout")):
        result = web_fetch("http://example.com")
    assert "Error" in result


def test_web_fetch_strips_output():
    mock_result = _make_completed_process(stdout="  trimmed  ")
    with patch("app.tools.web_fetch.subprocess.run", return_value=mock_result):
        result = web_fetch("http://example.com")
    assert result == "trimmed"


def test_web_fetch_uses_curl_http_failure_and_connect_timeout_flags():
    mock_result = _make_completed_process(stdout="ok")
    with patch("app.tools.web_fetch.subprocess.run", return_value=mock_result) as mock_run:
        web_fetch("http://example.com")

    mock_run.assert_called_once_with(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--connect-timeout",
            "5",
            "http://example.com",
        ],
        stdout=ANY,
        stderr=ANY,
        text=True,
        timeout=10,
    )
