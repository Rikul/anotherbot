import os

from app.tools.write_file import WriteFileTool

write_file = WriteFileTool.call


def test_write_file_creates_file(tmp_path):
    file_path = str(tmp_path / "output.txt")
    result = write_file(file_path, "hello")
    assert "Successfully wrote" in result
    with open(file_path, encoding="utf-8") as f:
        assert f.read() == "hello"


def test_write_file_creates_parent_directories(tmp_path):
    file_path = str(tmp_path / "subdir" / "nested" / "file.txt")
    result = write_file(file_path, "content")
    assert "Successfully wrote" in result
    assert os.path.isfile(file_path)


def test_write_file_overwrites_existing_file(tmp_path):
    file_path = str(tmp_path / "overwrite.txt")
    write_file(file_path, "original")
    write_file(file_path, "updated")
    with open(file_path, encoding="utf-8") as f:
        assert f.read() == "updated"


def test_write_file_empty_content(tmp_path):
    file_path = str(tmp_path / "empty.txt")
    result = write_file(file_path, "")
    assert "Successfully wrote" in result
    with open(file_path, encoding="utf-8") as f:
        assert f.read() == ""
