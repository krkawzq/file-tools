from pathlib import Path

import pytest

from file_tools.client import LocalClient
from file_tools.tools.read import (
    ReadError,
    ReadEmptyFileError,
    ReadFileNotFoundError,
    read,
)


def test_read_one_based_and_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\n")
    client = LocalClient(cwd=tmp_path)

    r = read("f.txt", offset=2, limit=2, client=client)
    assert r.total_lines == 4
    assert r.start_line == 2
    assert r.end_line == 3
    assert "b" in r.content
    assert "c" in r.content
    assert "2" in r.content  # line number


def test_tail_via_negative_offset(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("1\n2\n3\n4\n5\n")
    client = LocalClient(cwd=tmp_path)

    r = read("f.txt", offset=-2, show_line_numbers=False, client=client)
    assert r.content == "4\n5\n"
    assert r.start_line == 4
    assert r.end_line == 5


def test_empty_file_errors(tmp_path: Path) -> None:
    (tmp_path / "e.txt").write_text("")
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(ReadEmptyFileError):
        read("e.txt", client=client)


def test_missing(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    with pytest.raises(ReadFileNotFoundError):
        read("nope.txt", client=client)


def test_unknown_encoding_is_wrapped_as_read_error(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("content\n")

    with pytest.raises(ReadError, match="read_text failed"):
        read(
            "f.txt",
            encoding="not-a-real-encoding",
            client=LocalClient(cwd=tmp_path),
        )
