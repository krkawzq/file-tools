import os
from pathlib import Path

import pytest

from file_tools import LocalClient
from file_tools.tools.read import (
    ReadError,
    ReadEmptyFileError,
    ReadFileNotFoundError,
    read,
)

pytestmark = pytest.mark.anyio


async def test_read_one_based_and_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\n", newline="\n")
    client = LocalClient(cwd=tmp_path)

    r = await read("f.txt", offset=2, limit=2, client=client)
    assert r.total_lines == 4
    assert r.start_line == 2
    assert r.end_line == 3
    assert "b" in r.content
    assert "c" in r.content
    assert "2" in r.content  # line number


async def test_tail_via_negative_offset(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("1\n2\n3\n4\n5\n", newline="\n")
    client = LocalClient(cwd=tmp_path)

    r = await read("f.txt", offset=-2, show_line_numbers=False, client=client)
    assert r.content == "4\n5\n"
    assert r.start_line == 4
    assert r.end_line == 5


async def test_empty_file_errors(tmp_path: Path) -> None:
    (tmp_path / "e.txt").write_text("", newline="\n")
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(ReadEmptyFileError):
        await read("e.txt", client=client)


async def test_missing(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    with pytest.raises(ReadFileNotFoundError):
        await read("nope.txt", client=client)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="requires symlink support")
async def test_symlink_to_regular_file_is_followed(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("content\n", newline="\n")
    (tmp_path / "link.txt").symlink_to(target)

    result = await read(
        "link.txt",
        show_line_numbers=False,
        client=LocalClient(cwd=tmp_path),
    )

    assert result.content == "content\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO support")
async def test_non_regular_file_is_rejected_before_open(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)

    with pytest.raises(ReadError, match="not a regular file"):
        await read("pipe", client=LocalClient(cwd=tmp_path))


async def test_unknown_encoding_is_wrapped_as_read_error(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("content\n", newline="\n")

    with pytest.raises(ReadError, match="read_text failed"):
        await read(
            "f.txt",
            encoding="not-a-real-encoding",
            client=LocalClient(cwd=tmp_path),
        )
