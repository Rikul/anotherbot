import json
import pytest
from pathlib import Path
from unittest.mock import patch

from app.infra.tracer import write_trace


def test_write_trace_creates_file(tmp_path):
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    path = write_trace(messages, tmp_path, "test-model")
    assert path is not None
    assert path.exists()


def test_write_trace_file_content(tmp_path):
    messages = [{"role": "user", "content": "hello"}]
    path = write_trace(messages, tmp_path, "test-model")
    data = json.loads(path.read_text())
    assert data["model"] == "test-model"
    assert data["messages"] == messages
    assert "timestamp" in data


def test_write_trace_filename_format(tmp_path):
    path = write_trace([{"role": "user", "content": "hi"}], tmp_path, "m")
    assert path.name.startswith("trace_")
    assert path.suffix == ".json"


def test_write_trace_creates_missing_directory(tmp_path):
    tracedir = tmp_path / "nested" / "traces"
    path = write_trace([{"role": "user", "content": "hi"}], tracedir, "m")
    assert tracedir.exists()
    assert path is not None
    assert path.exists()


def test_write_trace_returns_none_on_error(tmp_path):
    # Create a file where the tracedir would need to be created as a directory
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file")
    result = write_trace([], blocker / "subdir", "m")
    assert result is None
