"""Bounded text-file reads for local and SSH filesystems."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .. import _core
from ..client import (
    Client,
    ClientError,
    resolve_client as _resolve_client,
    run_blocking,
)

DEFAULT_READ_LIMIT = 2000


class ReadError(Exception):
    """Base error raised while reading a file."""


class ReadFileNotFoundError(ReadError):
    """Raised when the requested file does not exist."""


class ReadIsDirectoryError(ReadError):
    """Raised when the requested path is a directory."""


class ReadEmptyFileError(ReadError):
    """Raised when the requested file is empty."""


@dataclass
class ReadResult:
    """Selected file content and its source line range."""

    file_path: str
    content: str
    total_lines: int
    start_line: int
    end_line: int
    lines: List[str] = field(default_factory=list)
    truncated: bool = False

    def __str__(self) -> str:
        return self.content

    def __bool__(self) -> bool:
        return len(self.content) > 0


async def read(
    file_path: str,
    *,
    offset: int = 1,
    limit: int = DEFAULT_READ_LIMIT,
    show_line_numbers: bool = True,
    encoding: str = "utf-8",
    client: Client | None = None,
) -> ReadResult:
    """Read a UTF-8 text file through a local or SSH client.

    Relative paths are resolved against ``client.cwd``. The target must exist,
    must be a regular file, and must contain at least one byte. Line selection
    uses 1-based positive offsets, treats zero as line 1, and uses
    ``offset=-N`` to return the final ``N`` lines while ignoring ``limit`` for
    the tail window.

    When ``show_line_numbers`` is true, the returned ``content`` uses
    ``cat -n``-style prefixes. A positive-offset read capped by ``limit`` also
    includes a final truncation notice. Set line numbers to false when exact
    source text is needed for a later :func:`edit` call. Decoding replaces
    invalid byte sequences instead of failing.

    Args:
        file_path: Absolute, home-relative, or client-cwd-relative file path.
        offset: 1-based starting line, zero for line 1, or a negative tail
            count. Defaults to 1.
        limit: Maximum returned lines for a non-negative offset. Must be
            greater than zero; defaults to 2000.
        show_line_numbers: Prefix lines and show a truncation notice when
            applicable. Defaults to true.
        encoding: Text encoding used by the client. Defaults to UTF-8.
        client: Existing local or SSH client. When omitted, create a local
            client rooted at the process working directory.

    Returns:
        A :class:`ReadResult` containing the resolved path, formatted content,
        raw selected lines, total line count, selected range, and truncation
        state.

    Raises:
        ValueError: If ``limit`` is not positive.
        ReadFileNotFoundError: If the target does not exist.
        ReadIsDirectoryError: If the target is a directory.
        ReadEmptyFileError: If the file is empty.
        ReadError: If the path is not a regular file or cannot be read.
    """
    if limit <= 0:
        raise ValueError(f"limit must be > 0: {limit}")

    c = _resolve_client(client)
    path = await c.resolve(file_path)

    if await c.is_dir(path):
        raise ReadIsDirectoryError(f"路径是目录而非文件: {path}")
    if not await c.is_file(path):
        if not await c.exists(path):
            raise ReadFileNotFoundError(f"文件不存在: {path}")
        raise ReadError(f"路径不是常规文件: {path}")

    try:
        text = await c.read_text(path, encoding=encoding)
    except ClientError as e:
        raise ReadError(str(e)) from e

    if text == "":
        raise ReadEmptyFileError(f"文件为空: {path}")

    content, total_lines, start_line, end_line, truncated, lines = await run_blocking(
        _core.prepare_read,
        text,
        offset,
        limit,
        show_line_numbers,
    )

    return ReadResult(
        file_path=path,
        content=content,
        total_lines=total_lines,
        start_line=start_line,
        end_line=end_line,
        lines=lines,
        truncated=truncated,
    )


__all__ = ["read"]
