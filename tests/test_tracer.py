import json
import pytest
from pathlib import Path
from unittest.mock import patch

from app.infra.tracer import write_trace


def test_write_trace_creates_file(tmp_path):
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    with patch("app.core.runtime._store", {"tracedir": tmp_path, "model": "test-model"}):
        path = write_trace(messages)
    assert path is not None
    assert path.exists()


def test_write_trace_file_content(tmp_path):
    messages = [{"role": "user", "content": "hello"}]
    with patch("app.core.runtime._store", {"tracedir": tmp_path, "model": "test-model"}):
        path = write_trace(messages)
    data = json.loads(path.read_text())
    assert data["model"] == "test-model"
    assert data["messages"] == messages
    assert "timestamp" in data


def test_write_trace_filename_format(tmp_path):
    with patch("app.core.runtime._store", {"tracedir": tmp_path, "model": "m"}):
        path = write_trace([{"role": "user", "content": "hi"}])
    assert path.name.startswith("trace_")
    assert path.suffix == ".json"


def test_write_trace_creates_missing_directory(tmp_path):
    tracedir = tmp_path / "nested" / "traces"
    with patch("app.core.runtime._store", {"tracedir": tracedir, "model": "m"}):
        path = write_trace([{"role": "user", "content": "hi"}])
    assert tracedir.exists()
    assert path is not None
    assert path.exists()


def test_write_trace_returns_none_on_error(tmp_path):
    # Create a file where the tracedir would need to be created as a directory
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file")
    with patch("app.core.runtime._store", {"tracedir": blocker / "subdir", "model": "m"}):
        result = write_trace([])
    assert result is None
