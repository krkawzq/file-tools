"""Whole-file text writes for local and SSH filesystems."""

from __future__ import annotations

from dataclasses import dataclass

from ..client import Client, ClientError, resolve_client as _resolve_client


class WriteError(Exception):
    """Base error raised while writing a file."""


class WriteIsDirectoryError(WriteError):
    """Raised when the destination is an existing directory."""


@dataclass
class WriteResult:
    """Resolved destination and write outcome."""

    file_path: str
    bytes_written: int
    is_new_file: bool
    overwrote: bool

    def __bool__(self) -> bool:
        return self.bytes_written > 0 or self.is_new_file


async def write(
    file_path: str,
    content: str,
    *,
    encoding: str = "utf-8",
    client: Client | None = None,
) -> WriteResult:
    """Create or completely overwrite a UTF-8 text file.

    Relative paths are resolved against ``client.cwd`` and missing parent
    directories are created automatically. Existing regular files are replaced
    in full without a backup or read-before-write guard. ``content`` is written
    exactly as supplied: no trailing newline is added, and an empty string
    creates or truncates the target to a zero-byte file.

    Use this tool only when the entire desired file content is known. Prefer
    :func:`edit` for a small unique replacement and :func:`apply_patch` for
    coordinated multi-file changes.

    Args:
        file_path: Absolute, home-relative, or client-cwd-relative destination.
        content: Complete replacement text.
        encoding: Encoding used to serialize ``content``. Defaults to UTF-8.
        client: Existing local or SSH client. When omitted, create a local
            client rooted at the process working directory.

    Returns:
        A :class:`WriteResult` containing the resolved path, UTF-8 byte count,
        and whether the operation created or overwrote the file.

    Raises:
        WriteIsDirectoryError: If the destination is an existing directory.
        WriteError: If encoding, directory creation, or writing fails.

    Warning:
        Writing an existing file irreversibly replaces its previous contents.
    """
    c = _resolve_client(client)
    path = await c.resolve(file_path)

    exists = await c.exists(path)
    if exists and await c.is_dir(path):
        raise WriteIsDirectoryError(f"Destination path is an existing directory: {path}")

    is_new = not exists
    try:
        encoded = content.encode(encoding)
    except (AttributeError, UnicodeError, LookupError) as e:
        raise WriteError(
            f"Failed to serialize content with encoding {encoding!r}: {e}"
        ) from e
    try:
        await c.write_text(path, content, encoding=encoding)
    except ClientError as e:
        raise WriteError(str(e)) from e

    return WriteResult(
        file_path=path,
        bytes_written=len(encoded),
        is_new_file=is_new,
        overwrote=not is_new,
    )


__all__ = ["write"]
