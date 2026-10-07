import logging
import logging.handlers
import sys
from unittest.mock import patch

import pytest

from app.infra.app_logging import AnsiFormatter, PlainFormatter, setup_logging


def _handlers_passed_to_basic_config(tmp_path, **kwargs) -> list[logging.Handler]:
    with patch("app.infra.app_logging.LOG_DIR", tmp_path), \
         patch("logging.basicConfig") as basic_config:
        setup_logging(**kwargs)
    handlers = basic_config.call_args.kwargs["handlers"]
    for h in handlers:
        h.close()
    return handlers


def test_setup_logging_adds_console_and_file_handlers_by_default(tmp_path):
    handlers = _handlers_passed_to_basic_config(tmp_path)
    kinds = {type(h) for h in handlers}
    assert kinds == {logging.StreamHandler, logging.handlers.RotatingFileHandler}


def test_setup_logging_console_false_only_logs_to_file(tmp_path):
    handlers = _handlers_passed_to_basic_config(tmp_path, console=False)
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.handlers.RotatingFileHandler)
    assert handlers[0].baseFilename == str(tmp_path / "app.log")


@pytest.mark.parametrize("formatter", [AnsiFormatter(), PlainFormatter()])
def test_formatters_include_exception_traceback(formatter):
    try:
        raise ValueError("broken startup")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "app.main", logging.ERROR, __file__, 1, "Application failed", (), exc_info
    )
    formatted = formatter.format(record)

    assert "Traceback (most recent call last)" in formatted
    assert "ValueError: broken startup" in formatted
