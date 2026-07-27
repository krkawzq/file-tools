from pathlib import Path

import pytest

from file_tools import LocalClient
from file_tools.tools.edit import (
    EditAmbiguousMatchError,
    EditFileExistsError,
    EditFileNotFoundError,
    EditStringNotFoundError,
    edit,
)

pytestmark = pytest.mark.anyio


async def test_replaces_a_unique_match_and_returns_metadata(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("hello world\n", newline="\n")
    client = LocalClient(cwd=tmp_path)

    result = await edit("example.txt", "hello", "hi", client=client)

    assert target.read_text() == "hi world\n"
    assert result.replacements == 1
    assert not result.is_new_file
    assert result


async def test_does_not_add_a_trailing_newline_after_replacement(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("before", newline="\n")
    client = LocalClient(cwd=tmp_path)

    await edit("example.txt", "before", "after", client=client)

    assert target.read_text() == "after"


async def test_replacement_can_remove_a_trailing_newline(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("before\n", newline="\n")
    client = LocalClient(cwd=tmp_path)

    await edit("example.txt", "before\n", "after", client=client)

    assert target.read_text() == "after"


async def test_explicitly_prepends_when_old_string_is_empty(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("body", newline="\n")
    client = LocalClient(cwd=tmp_path)

    result = await edit("example.txt", "", "header\n", prepend=True, client=client)

    assert target.read_text() == "header\nbody"
    assert result.operation == "prepended"
    assert result.replacements == 0
    assert not result.is_new_file
    assert result


async def test_empty_old_string_rejects_existing_file_without_explicit_prepend(
    tmp_path: Path,
) -> None:
    target = tmp_path / "example.txt"
    target.write_text("body", newline="\n")

    with pytest.raises(EditFileExistsError, match="prepend=True"):
        await edit(
            "example.txt",
            "",
            "header\n",
            client=LocalClient(cwd=tmp_path),
        )

    assert target.read_text() == "body"


async def test_creates_a_new_file_and_parent_directories(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    target = tmp_path / "nested" / "example.txt"

    result = await edit("nested/example.txt", "", "new content", client=client)

    assert target.read_text() == "new content"
    assert result.replacements == 0
    assert result.is_new_file
    assert result.operation == "created"
    assert result


async def test_can_create_an_empty_file(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)

    result = await edit("empty.txt", "", "", client=client)

    assert (tmp_path / "empty.txt").read_text() == ""
    assert result.is_new_file
    assert result


async def test_nonempty_old_string_requires_an_existing_file(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(EditFileNotFoundError):
        await edit("missing.txt", "a", "b", client=client)


async def test_prepend_requires_empty_old_string_and_existing_file(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    (tmp_path / "existing.txt").write_text("body", newline="\n")

    with pytest.raises(ValueError, match="old_string"):
        await edit(
            "existing.txt",
            "body",
            "header",
            prepend=True,
            client=client,
        )

    with pytest.raises(ValueError, match="replace_all"):
        await edit(
            "existing.txt",
            "",
            "header",
            prepend=True,
            replace_all=True,
            client=client,
        )

    with pytest.raises(EditFileNotFoundError, match="existing file"):
        await edit("missing.txt", "", "header", prepend=True, client=client)


async def test_not_found(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("line1\nline2\n", newline="\n")
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(EditStringNotFoundError):
        await edit("f.txt", "nonexistent", "x", client=client)


async def test_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("dup\nmiddle\ndup\n", newline="\n")
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(EditAmbiguousMatchError):
        await edit("f.txt", "dup", "new", client=client)


async def test_replace_all(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("dup\nmiddle\ndup\n", newline="\n")
    client = LocalClient(cwd=tmp_path)

    result = await edit("f.txt", "dup", "NEW", replace_all=True, client=client)
    assert result.replacements == 2
    assert (tmp_path / "f.txt").read_text() == "NEW\nmiddle\nNEW\n"


async def test_trailing_whitespace_tolerance(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello   \nworld\t\n", newline="\n")
    client = LocalClient(cwd=tmp_path)

    await edit("f.txt", "hello\nworld", "hi\nthere", client=client)
    assert (tmp_path / "f.txt").read_text() == "hi\nthere\n"


async def test_edit_and_prepend_preserve_crlf(tmp_path: Path) -> None:
    target = tmp_path / "windows.txt"
    target.write_bytes(b"hello  \r\nworld\r\n")
    client = LocalClient(cwd=tmp_path)

    await edit("windows.txt", "hello\nworld", "hi\nthere", client=client)
    assert target.read_bytes() == b"hi\r\nthere\r\n"

    await edit("windows.txt", "", "header\n", prepend=True, client=client)
    assert target.read_bytes() == b"header\r\nhi\r\nthere\r\n"
