from app.tools.read_file import ReadFileTool

read_file = ReadFileTool.call


def test_read_file_returns_contents(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world", encoding="utf-8")
    assert read_file(str(f)) == "hello world"


def test_read_file_nonexistent_returns_error():
    result = read_file("/nonexistent/path/file.txt")
    assert result.startswith("Error:")


def test_read_file_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    assert read_file(str(f)) == ""


def test_read_file_multiline(tmp_path):
    content = "line1\nline2\nline3"
    f = tmp_path / "multi.txt"
    f.write_text(content, encoding="utf-8")
    assert read_file(str(f)) == content


def test_read_file_with_offset(tmp_path):
    f = tmp_path / "offset.txt"
    f.write_text("hello world", encoding="utf-8")
    assert read_file(str(f), offset=6) == "world"


def test_read_file_with_size(tmp_path):
    f = tmp_path / "size.txt"
    f.write_text("hello world", encoding="utf-8")
    assert read_file(str(f), size=5) == "hello"


def test_read_file_with_offset_and_size(tmp_path):
    f = tmp_path / "both.txt"
    f.write_text("hello world", encoding="utf-8")
    assert read_file(str(f), offset=3, size=4) == "lo w"


def test_read_file_offset_beyond_end(tmp_path):
    f = tmp_path / "short.txt"
    f.write_text("hi", encoding="utf-8")
    assert read_file(str(f), offset=100) == ""


def test_read_file_size_larger_than_remaining(tmp_path):
    f = tmp_path / "small.txt"
    f.write_text("hello", encoding="utf-8")
    assert read_file(str(f), offset=3, size=100) == "lo"


def test_read_file_negative_offset_or_size(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello", encoding="utf-8")
    assert "Error" in read_file(str(f), offset=-1)
    assert "Error" in read_file(str(f), size=-1)


def test_read_file_utf8_split(tmp_path):
    f = tmp_path / "utf8.txt"
    f.write_bytes(b"abc\xf0\x9f\x9a\x80def")
    assert read_file(str(f), offset=3, size=2) == "\ufffd"


def test_read_file_refuses_special_files(tmp_path):
    import os

    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)  # opening it with no writer would block forever
        assert read_file(str(fifo)) == f"Error: {fifo} is not a regular file"
    assert read_file(str(tmp_path)) == f"Error: {tmp_path} is not a regular file"


def test_read_file_reads_at_most_the_cap(tmp_path, monkeypatch):
    from app.tools import read_file as module

    monkeypatch.setattr(module, "MAX_READ_BYTES", 5)
    f = tmp_path / "big.txt"
    f.write_text("0123456789")
    assert (
        read_file(str(f))
        == "01234\n… stopped after 5 bytes; read further with offset 5"
    )
    assert read_file(str(f), offset=5).startswith("56789")
    assert read_file(str(f), size=3) == "012"
    assert read_file(str(f), offset=5, size=5) == "56789"
