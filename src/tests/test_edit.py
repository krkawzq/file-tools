from pathlib import Path

import pytest

from file_tools.client import ClientError, LocalClient
from file_tools.tools.edit import (
    EditAmbiguousMatchError,
    EditError,
    EditFileExistsError,
    EditFileNotFoundError,
    EditResult,
    EditStringNotFoundError,
    edit,
)


def test_replaces_a_unique_match_and_returns_metadata(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("hello world\n")
    client = LocalClient(cwd=tmp_path)

    result = edit("example.txt", "hello", "hi", client=client)

    assert target.read_text() == "hi world\n"
    assert result.replacements == 1
    assert not result.is_new_file
    assert result


def test_does_not_add_a_trailing_newline_after_replacement(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("before")
    client = LocalClient(cwd=tmp_path)

    edit("example.txt", "before", "after", client=client)

    assert target.read_text() == "after"


def test_replacement_can_remove_a_trailing_newline(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("before\n")
    client = LocalClient(cwd=tmp_path)

    edit("example.txt", "before\n", "after", client=client)

    assert target.read_text() == "after"


def test_explicitly_prepends_when_old_string_is_empty(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("body")
    client = LocalClient(cwd=tmp_path)

    result = edit("example.txt", "", "header\n", prepend=True, client=client)

    assert target.read_text() == "header\nbody"
    assert result.operation == "prepended"
    assert result.replacements == 0
    assert not result.is_new_file
    assert result


def test_empty_old_string_rejects_existing_file_without_explicit_prepend(
    tmp_path: Path,
) -> None:
    target = tmp_path / "example.txt"
    target.write_text("body")

    with pytest.raises(EditFileExistsError, match="prepend=True"):
        edit(
            "example.txt",
            "",
            "header\n",
            client=LocalClient(cwd=tmp_path),
        )

    assert target.read_text() == "body"


def test_prepend_wraps_client_write_errors(tmp_path: Path) -> None:
    class FailingWriteClient(LocalClient):
        def write_text(
            self, path: str, content: str, *, encoding: str = "utf-8"
        ) -> None:
            raise ClientError("write failed")

    target = tmp_path / "example.txt"
    target.write_text("body")

    with pytest.raises(EditError, match="write failed"):
        edit(
            "example.txt",
            "",
            "header\n",
            prepend=True,
            client=FailingWriteClient(tmp_path),
        )

    assert target.read_text() == "body"


def test_creates_a_new_file_and_parent_directories(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    target = tmp_path / "nested" / "example.txt"

    result = edit("nested/example.txt", "", "new content", client=client)

    assert target.read_text() == "new content"
    assert result.replacements == 0
    assert result.is_new_file
    assert result.operation == "created"
    assert result


def test_can_create_an_empty_file(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)

    result = edit("empty.txt", "", "", client=client)

    assert (tmp_path / "empty.txt").read_text() == ""
    assert result.is_new_file
    assert result


def test_rejects_creation_when_allow_empty_file_is_false(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(EditFileNotFoundError, match="allow_empty_file=False"):
        edit("missing.txt", "", "content", allow_empty_file=False, client=client)

    assert not (tmp_path / "missing.txt").exists()


def test_nonempty_old_string_requires_an_existing_file(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(EditFileNotFoundError):
        edit("missing.txt", "a", "b", client=client)


def test_prepend_requires_empty_old_string_and_existing_file(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    (tmp_path / "existing.txt").write_text("body")

    with pytest.raises(ValueError, match="old_string"):
        edit(
            "existing.txt",
            "body",
            "header",
            prepend=True,
            client=client,
        )

    with pytest.raises(ValueError, match="replace_all"):
        edit(
            "existing.txt",
            "",
            "header",
            prepend=True,
            replace_all=True,
            client=client,
        )

    with pytest.raises(EditFileNotFoundError, match="已存在"):
        edit("missing.txt", "", "header", prepend=True, client=client)


def test_not_found(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("line1\nline2\n")
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(EditStringNotFoundError):
        edit("f.txt", "nonexistent", "x", client=client)


def test_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("dup\nmiddle\ndup\n")
    client = LocalClient(cwd=tmp_path)

    with pytest.raises(EditAmbiguousMatchError):
        edit("f.txt", "dup", "new", client=client)


def test_replace_all(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("dup\nmiddle\ndup\n")
    client = LocalClient(cwd=tmp_path)

    result = edit("f.txt", "dup", "NEW", replace_all=True, client=client)
    assert result.replacements == 2
    assert (tmp_path / "f.txt").read_text() == "NEW\nmiddle\nNEW\n"


def test_trailing_whitespace_tolerance(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello   \nworld\t\n")
    client = LocalClient(cwd=tmp_path)

    edit("f.txt", "hello\nworld", "hi\nthere", client=client)
    assert (tmp_path / "f.txt").read_text() == "hi\nthere\n"


def test_edit_and_prepend_preserve_crlf(tmp_path: Path) -> None:
    target = tmp_path / "windows.txt"
    target.write_bytes(b"hello  \r\nworld\r\n")
    client = LocalClient(cwd=tmp_path)

    edit("windows.txt", "hello\nworld", "hi\nthere", client=client)
    assert target.read_bytes() == b"hi\r\nthere\r\n"

    edit("windows.txt", "", "header\n", prepend=True, client=client)
    assert target.read_bytes() == b"header\r\nhi\r\nthere\r\n"
