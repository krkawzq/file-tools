"""MCP tool implementations as plain functions (no FastMCP decorators).

These functions are the callable surface used by :mod:`register` and by
hosts that import the plain API. Agent-facing prose lives primarily on the
``@mcp.tool`` wrappers in ``register.py``; keep parameter contracts aligned.

Common parameters on every tool:

- ``cwd`` (**required**) — workspace root on the selected client
- ``client``: ``"local"`` (default) | ``"ssh"``
- When ``client="ssh"``: require ``ssh_host``, positive ``ssh_port``,
  ``ssh_user``; optional ``ssh_password`` / ``ssh_key`` / ``ssh_flags``
- Unknown host keys are rejected unless
  ``ssh_accept_unknown_host_key=True``
"""

from __future__ import annotations

from typing import Mapping

from ..client import get_cached_client as _get_cached_client
from ..tools.apply_patch import apply_patch as _apply_patch
from ..tools.bash import bash as _bash
from ..tools.edit import edit as _edit
from ..tools.read import read as _read
from ..tools.write import write as _write


def _require_cwd(cwd: str) -> str:
    c = (cwd or "").strip()
    if not c:
        raise ValueError("cwd is required")
    return c


def _client(
    *,
    cwd: str,
    client: str = "local",
    ssh_host: str = "",
    ssh_port: int | None = None,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
    ssh_flags: str = "",
    ssh_accept_unknown_host_key: bool = False,
):
    kind = str(client).strip().lower()
    settings: dict[str, object] = {"client": kind, "cwd": cwd}
    if kind == "ssh":
        settings.update(
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key=ssh_key,
            ssh_flags=ssh_flags,
            accept_unknown_host_key=ssh_accept_unknown_host_key,
        )
    return _get_cached_client(**settings)


async def read(
    target_file: str,
    cwd: str,
    offset: int = 1,
    limit: int = 2000,
    show_line_numbers: bool = True,
    client: str = "local",
    ssh_host: str = "",
    ssh_port: int | None = None,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
    ssh_flags: str = "",
    ssh_accept_unknown_host_key: bool = False,
) -> str:
    """Read a window of UTF-8 text from a local or SSH filesystem.

    Reads up to ``limit`` lines (default 2000) from ``offset``. With
    ``show_line_numbers=True`` (default), lines use ``cat -n`` prefixes and a
    truncation notice may follow a limited window. Set line numbers false for
    exact source text. Backends retain only the requested window; SSH transfers
    selected lines only.

    ``offset`` is 1-based (``0`` → 1). Negative ``offset`` tails the last N
    lines and ignores ``limit`` for window size. Target must be a non-empty
    regular file; UTF-8 decoding replaces invalid bytes. Symlinks resolving to
    regular files are followed, while FIFOs/devices/other special files raise.

    The selected text window is limited to 16 MiB. SSH connection and
    individual file operations use 30-second timeouts.

    Args:
        target_file: Absolute, ``~``-relative, or ``cwd``-relative path.
        cwd: Required working directory for relative resolution.
        offset: 1-based start, or negative tail count. Defaults to 1.
        limit: Max lines for non-negative offset (``> 0``, default 2000).
        show_line_numbers: Prefix lines / truncation notice. Defaults True.
        client: ``"local"`` (default) or ``"ssh"``.
        ssh_host: Host / IP / ``~/.ssh/config`` alias when ``client="ssh"``.
        ssh_port: Positive port when ``client="ssh"``. No implicit 22.
        ssh_user: Username when ``client="ssh"``.
        ssh_password: Optional password.
        ssh_key: Optional private-key path.
        ssh_flags: Supported OpenSSH flags: ``-X``, ``-Y``, ``-A``, ``-a``,
            ``-C``.
        ssh_accept_unknown_host_key: Trust missing host keys (insecure).

    Returns:
        Selected text, optionally line-numbered.

    Raises:
        ValueError: Empty ``cwd``, bad ``limit``/client, missing SSH settings.
        ReadFileNotFoundError / ReadIsDirectoryError / ReadEmptyFileError /
        ReadError: Path or read failures.
    """
    cwd = _require_cwd(cwd)
    c = _client(
        cwd=cwd,
        client=client,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key=ssh_key,
        ssh_flags=ssh_flags,
        ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
    )
    result = await _read(
        target_file,
        offset=offset,
        limit=limit,
        show_line_numbers=show_line_numbers,
        client=c,
    )
    return result.content


async def write(
    file_path: str,
    content: str,
    cwd: str,
    client: str = "local",
    ssh_host: str = "",
    ssh_port: int | None = None,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
    ssh_flags: str = "",
    ssh_accept_unknown_host_key: bool = False,
) -> str:
    """Create a UTF-8 text file or replace it entirely.

    Creates missing parents. Overwrites existing regular files without backup.
    Writes ``content`` exactly (no auto trailing newline; empty → zero bytes).
    The encoded payload is limited to 16 MiB. SSH connection and individual
    file operations use 30-second timeouts.

    Args:
        file_path: Absolute, ``~``-relative, or ``cwd``-relative destination.
        content: Complete file body (UTF-8).
        cwd: Required working directory.
        client: ``"local"`` (default) or ``"ssh"``.
        ssh_host: Host / IP / alias when ``client="ssh"``.
        ssh_port: Positive port when ``client="ssh"``. No implicit 22.
        ssh_user: Username when ``client="ssh"``.
        ssh_password: Optional password.
        ssh_key: Optional private-key path.
        ssh_flags: Supported OpenSSH flags: ``-X``, ``-Y``, ``-A``, ``-a``,
            ``-C``.
        ssh_accept_unknown_host_key: Trust missing host keys (insecure).

    Returns:
        ``"wrote N bytes to <path>"``.

    Raises:
        ValueError: Empty ``cwd``, bad client, missing SSH settings.
        WriteIsDirectoryError / WriteError: Destination or I/O failures.

    Warning:
        Destructive for existing files.
    """
    cwd = _require_cwd(cwd)
    c = _client(
        cwd=cwd,
        client=client,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key=ssh_key,
        ssh_flags=ssh_flags,
        ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
    )
    result = await _write(file_path, content, client=c)
    return f"wrote {result.bytes_written} bytes to {result.file_path}"


async def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    cwd: str,
    replace_all: bool = False,
    prepend: bool = False,
    client: str = "local",
    ssh_host: str = "",
    ssh_port: int | None = None,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
    ssh_flags: str = "",
    ssh_accept_unknown_host_key: bool = False,
) -> str:
    """Literal string edit, new-file create, or explicit prepend.

    Matching: exact substring first, then per-line trailing-whitespace
    tolerance. Default unique match; ``replace_all=true`` for all occurrences.
    ``old_string=""`` creates a new file (fails if exists) or, with
    ``prepend=true``, prepends to an existing file. No append mode — use
    ``apply_patch`` for EOF append. No auto trailing newline. When an existing
    target contains CRLF, LF in match/replacement/prepend text is accepted and
    written as CRLF. Each file read/write is limited to 16 MiB; SSH connection
    and individual file operations use 30-second timeouts.

    Args:
        file_path: Absolute, ``~``-relative, or ``cwd``-relative path.
        old_string: Match text, or ``""`` for create/prepend.
        new_string: Replacement, new body, or prepend prefix. LF is normalized
            to CRLF when the existing target uses CRLF.
        cwd: Required working directory.
        replace_all: Replace all non-overlapping matches. Defaults false.
        prepend: Prepend mode; requires ``old_string=""``. Defaults false.
        client: ``"local"`` (default) or ``"ssh"``.
        ssh_host: Host / IP / alias when ``client="ssh"``.
        ssh_port: Positive port when ``client="ssh"``. No implicit 22.
        ssh_user: Username when ``client="ssh"``.
        ssh_password: Optional password.
        ssh_key: Optional private-key path.
        ssh_flags: Supported OpenSSH flags: ``-X``, ``-Y``, ``-A``, ``-a``,
            ``-C``.
        ssh_accept_unknown_host_key: Trust missing host keys (insecure).

    Returns:
        ``created`` / ``prepended to`` / ``replaced ... (N matches)`` summary.

    Raises:
        ValueError: Bad cwd/client/SSH or invalid prepend combination.
        EditFileNotFoundError / EditFileExistsError /
        EditStringNotFoundError / EditAmbiguousMatchError / EditError.
    """
    cwd = _require_cwd(cwd)
    c = _client(
        cwd=cwd,
        client=client,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key=ssh_key,
        ssh_flags=ssh_flags,
        ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
    )
    result = await _edit(
        file_path,
        old_string,
        new_string,
        replace_all=replace_all,
        prepend=prepend,
        client=c,
    )
    if result.operation == "replaced":
        return f"replaced {result.file_path} ({result.replacements} matches)"
    if result.operation == "created":
        return f"created {result.file_path}"
    return f"prepended to {result.file_path}"


async def apply_patch(
    patch_text: str,
    cwd: str,
    client: str = "local",
    ssh_host: str = "",
    ssh_port: int | None = None,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
    ssh_flags: str = "",
    ssh_accept_unknown_host_key: bool = False,
) -> str:
    """Apply a structured multi-file patch (add / update / delete / move).

    Preflighted before writes; low-level commit failures get best-effort
    deterministic rollback. Format is not unified diff: ``***`` / ``@@`` in
    column 1; Update content lines need space/``-``/``+`` prefixes. Bare
    ``@@`` with only ``+`` lines appends at EOF. See the MCP tool docstring on
    the registered ``apply_patch`` wrapper for full examples. Each file
    read/write is limited to 16 MiB; SSH connection and individual file
    operations use 30-second timeouts. Move-only hunks preserve decoded text
    and newline shape for valid UTF-8; invalid bytes are replaced.

    Args:
        patch_text: Full document including Begin/End Patch markers.
        cwd: Required working directory; relative patch paths resolve here.
        client: ``"local"`` (default) or ``"ssh"``.
        ssh_host: Host / IP / alias when ``client="ssh"``.
        ssh_port: Positive port when ``client="ssh"``. No implicit 22.
        ssh_user: Username when ``client="ssh"``.
        ssh_password: Optional password.
        ssh_key: Optional private-key path.
        ssh_flags: Supported OpenSSH flags: ``-X``, ``-Y``, ``-A``, ``-a``,
            ``-C``.
        ssh_accept_unknown_host_key: Trust missing host keys (insecure).

    Returns:
        ``added=... modified=... deleted=...`` path summary.

    Raises:
        ValueError: Empty ``cwd``, bad client, missing SSH settings.
        PatchParseError / PatchSeekError / PatchApplyError.

    Warning:
        Delete and move remove sources after destination writes.
    """
    cwd = _require_cwd(cwd)
    c = _client(
        cwd=cwd,
        client=client,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key=ssh_key,
        ssh_flags=ssh_flags,
        ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
    )
    result = await _apply_patch(patch_text, client=c)
    return f"added={result.added} modified={result.modified} deleted={result.deleted}"


async def bash(
    command: str,
    cwd: str,
    timeout: float = 120.0,
    description: str = "",
    interpreter: str = "auto",
    flags: str = "",
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
    max_output_bytes: int = 1024 * 1024,
    client: str = "local",
    ssh_host: str = "",
    ssh_port: int | None = None,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
    ssh_flags: str = "",
    ssh_accept_unknown_host_key: bool = False,
) -> str:
    """Run a bounded foreground command in an explicit workspace.

    Default interpreter is ``cmd`` on local Windows, ``bash`` elsewhere
    (including SSH). Full shell semantics; no sandbox or background manager.
    Timeout default 120s (``0`` disables); timeout → exit 124 with partial
    output. Output retained per stream up to ``max_output_bytes``.

    Args:
        command: Non-empty command or script.
        cwd: Required working directory.
        timeout: Seconds; ``0`` disables. Defaults 120.
        description: Optional report label.
        interpreter: Executable or ``auto``.
        flags: Extra interpreter flags (command-string flag injected).
        env: Optional env overrides.
        stdin: Optional UTF-8 stdin (then closed).
        max_output_bytes: Per-stream retain limit (default 1 MiB, max 16 MiB).
        client: ``"local"`` (default) or ``"ssh"``.
        ssh_host: Host / IP / alias when ``client="ssh"``.
        ssh_port: Positive port when ``client="ssh"``. No implicit 22.
        ssh_user: Username when ``client="ssh"``.
        ssh_password: Optional password.
        ssh_key: Optional private-key path.
        ssh_flags: Supported OpenSSH flags: ``-X``, ``-Y``, ``-A``, ``-a``,
            ``-C``.
        ssh_accept_unknown_host_key: Trust missing host keys (insecure).

    Returns:
        Formatted report (exit, cwd, duration, command, stdout/stderr).

    Raises:
        ValueError: Empty ``cwd``, bad client, missing SSH settings.
        BashError: Invalid args or start failure.

    Warning:
        Arbitrary code execution with the selected client's permissions.
    """
    cwd = _require_cwd(cwd)
    c = _client(
        cwd=cwd,
        client=client,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key=ssh_key,
        ssh_flags=ssh_flags,
        ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
    )
    result = await _bash(
        command,
        cwd=cwd,
        timeout=timeout,
        description=description,
        interpreter=interpreter,
        flags=flags,
        env=env,
        stdin=stdin,
        max_output_bytes=max_output_bytes,
        client=c,
    )
    return result.format()
