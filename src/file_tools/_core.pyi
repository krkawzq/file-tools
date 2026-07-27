"""Native local/SSH clients and text-processing primitives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from os import PathLike
from typing import TypeAlias

class ClientError(Exception): ...
class FileNotFoundError(ClientError): ...
class PermissionDeniedError(ClientError): ...
class ConflictError(ClientError): ...
class OperationTimeoutError(ClientError): ...
class AuthenticationError(ClientError): ...
class TransferLimitError(ClientError): ...

class FileInfo:
    @property
    def exists(self) -> bool: ...
    @property
    def kind(self) -> str: ...
    @property
    def size(self) -> int: ...
    @property
    def modified_ns(self) -> int | None: ...
    @property
    def is_symlink(self) -> bool: ...
    @property
    def version(self) -> str | None: ...

class CommandResult:
    @property
    def exit_code(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...
    @property
    def command(self) -> str: ...
    @property
    def cwd(self) -> str: ...
    @property
    def duration_ms(self) -> int: ...
    @property
    def timed_out(self) -> bool: ...
    @property
    def signal(self) -> int | None: ...
    @property
    def stdout_total_bytes(self) -> int: ...
    @property
    def stderr_total_bytes(self) -> int: ...
    @property
    def stdout_omitted_bytes(self) -> int: ...
    @property
    def stderr_omitted_bytes(self) -> int: ...
    @property
    def extras(self) -> dict[str, str]: ...
    @property
    def ok(self) -> bool: ...
    @property
    def truncated(self) -> bool: ...
    def __bool__(self) -> bool: ...

class LocalClient:
    kind: str
    def __init__(
        self,
        cwd: str | PathLike[str] | None = None,
        *,
        max_transfer_bytes: int = 16 * 1024 * 1024,
    ) -> None: ...
    @property
    def cwd(self) -> str: ...
    def resolve(self, path: str) -> str: ...
    def exists(self, path: str) -> bool: ...
    def is_file(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def path_info(self, path: str) -> tuple[bool, bool, bool]: ...
    def stat(self, path: str) -> FileInfo: ...
    def read_text(self, path: str, *, encoding: str = "utf-8") -> str: ...
    def read_text_window(
        self,
        path: str,
        offset: int,
        limit: int,
        *,
        encoding: str = "utf-8",
    ) -> tuple[str, int, int, int, bool]: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_text(
        self, path: str, content: str, *, encoding: str = "utf-8"
    ) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text_atomic(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        expected_version: str | None = None,
        create_only: bool = False,
    ) -> FileInfo: ...
    def mkdir(
        self, path: str, *, parents: bool = True, exist_ok: bool = True
    ) -> None: ...
    def delete(self, path: str) -> None: ...
    def delete_if_version(
        self, path: str, *, expected_version: str | None = None
    ) -> None: ...
    def join(self, *parts: str) -> str: ...
    def exec_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
        interpreter: str | None = None,
        flags: str | Sequence[str] | None = None,
        max_output_bytes: int = 1024 * 1024,
    ) -> CommandResult: ...
class SshClient:
    kind: str
    def __init__(
        self,
        host: str,
        *,
        port: int,
        username: str,
        password: str | None = None,
        key_filename: str | None = None,
        cwd: str = ".",
        connect_timeout: float = 30.0,
        operation_timeout: float = 30.0,
        max_transfer_bytes: int = 16 * 1024 * 1024,
        multiplexing: bool = True,
        ssh_flags: str | Sequence[str] | None = None,
        allow_password_prompt: bool = True,
        accept_unknown_host_key: bool = False,
    ) -> None: ...
    @property
    def cwd(self) -> str: ...
    def resolve(self, path: str) -> str: ...
    def exists(self, path: str) -> bool: ...
    def is_file(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def path_info(self, path: str) -> tuple[bool, bool, bool]: ...
    def stat(self, path: str) -> FileInfo: ...
    def read_text(self, path: str, *, encoding: str = "utf-8") -> str: ...
    def read_text_window(
        self,
        path: str,
        offset: int,
        limit: int,
        *,
        encoding: str = "utf-8",
    ) -> tuple[str, int, int, int, bool]: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_text(
        self, path: str, content: str, *, encoding: str = "utf-8"
    ) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text_atomic(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        expected_version: str | None = None,
        create_only: bool = False,
    ) -> FileInfo: ...
    def mkdir(
        self, path: str, *, parents: bool = True, exist_ok: bool = True
    ) -> None: ...
    def delete(self, path: str) -> None: ...
    def delete_if_version(
        self, path: str, *, expected_version: str | None = None
    ) -> None: ...
    def join(self, *parts: str) -> str: ...
    def exec_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
        interpreter: str | None = None,
        flags: str | Sequence[str] | None = None,
        max_output_bytes: int = 1024 * 1024,
    ) -> CommandResult: ...
Client: TypeAlias = LocalClient | SshClient

def normalize_flags(
    flags: str | Sequence[str] | None = None, *, posix: bool | None = None
) -> list[str]: ...
def inject_cmd_flag(interpreter: str, flags: list[str]) -> list[str]: ...
def find_matches(text: str, pattern: str) -> list[tuple[int, int]]:
    """Find all matches as ``(byte_start, byte_len)`` pairs.

    Strategy: exact substring first, then per-line rstrip match.
    """
    ...

def apply_replacements_text(
    text: str,
    matches: list[tuple[int, int]],
    new_string: str,
) -> str:
    """Apply non-overlapping replacements at the given byte spans."""
    ...

def edit_text(
    text: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, int]:
    """Match + replace. Returns ``(new_text, replacement_count)``.

    Does not add or remove a trailing newline beyond the literal replacement.
    Raises ``ValueError`` with ``NOT_FOUND:`` / ``AMBIGUOUS:`` prefixes.
    """
    ...

def count_lines(text: str) -> int:
    """Count lines (non-empty file without trailing newline still counts last line)."""
    ...

def slice_lines(text: str, start: int, take: int) -> list[str]:
    """Slice lines by 0-based index; each line keeps its original ending."""
    ...

def format_cat_n(
    lines: list[str],
    start_line: int,
    total_lines: int,
    truncated: bool = False,
) -> str:
    """Format lines as ``cat -n`` (1-based numbers)."""
    ...

def prepare_read(
    text: str,
    offset: int,
    limit: int,
    show_line_numbers: bool = True,
) -> tuple[str, int, int, int, bool, list[str]]:
    """Prepare a bounded line window.

    Parameters
    ----------
    offset:
        1-based start line. Negative value ``-N`` means last N lines (tail).
    limit:
        Max lines to return (window size ignored when ``offset < 0``).

    Returns
    -------
    (content, total_lines, start_line, end_line, truncated, raw_lines)
    """
    ...

def seek_sequence(
    lines: list[str],
    pattern: list[str],
    start: int = 0,
    eof: bool = False,
) -> int | None:
    """Search for a line sequence with progressively relaxed normalization."""
    ...

def apply_line_replacements(
    lines: list[str],
    replacements: list[tuple[int, int, list[str]]],
) -> list[str]:
    """Apply ``(start_idx, old_len, new_lines)`` replacements.

    Raises ``ValueError`` for out-of-range or overlapping spans.
    """
    ...

def derive_new_contents(
    original_content: str,
    path: str,
    chunks: list[tuple[str | None, list[str], list[str], bool]],
) -> str:
    """Compute new file text from update chunks.

    Each chunk is ``(change_context, old_lines, new_lines, is_end_of_file)``.
    """
    ...
