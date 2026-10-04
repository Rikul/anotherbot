import logging
import logging.handlers
from unittest.mock import patch

from app.infra.app_logging import setup_logging


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
