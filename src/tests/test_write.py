from pathlib import Path

import pytest

from file_tools import LocalClient
from file_tools.tools.write import WriteError, write


def test_write_new_and_overwrite(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)

    r1 = write("a.txt", "hello\n", client=client)
    assert r1.is_new_file
    assert (tmp_path / "a.txt").read_text() == "hello\n"

    r2 = write("a.txt", "world\n", client=client)
    assert r2.overwrote
    assert (tmp_path / "a.txt").read_text() == "world\n"


def test_write_nested(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    write("x/y/z.txt", "deep\n", client=client)
    assert (tmp_path / "x/y/z.txt").read_text() == "deep\n"


def test_encoding_error_does_not_truncate_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original\n")

    with pytest.raises(WriteError, match="序列化"):
        write(
            "existing.txt",
            "cannot encode: é",
            encoding="ascii",
            client=LocalClient(cwd=tmp_path),
        )

    assert target.read_text() == "original\n"
