from unittest.mock import patch, mock_open

from app.core.agent import get_default_sys_prompt

def test_get_default_sys_prompt_returns_string():
    result = get_default_sys_prompt()
    assert isinstance(result, str)


def test_get_default_sys_prompt_includes_file_content():
    result = get_default_sys_prompt()
    assert len(result) > 0


def test_get_default_sys_prompt_skips_missing_file():
    with patch("builtins.open", side_effect=FileNotFoundError):
        result = get_default_sys_prompt()
    assert isinstance(result, str)
