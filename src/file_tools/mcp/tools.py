"""MCP tool implementations as plain functions (no FastMCP decorators).

Common parameters:

- ``cwd`` (**required**)
- ``client``: ``"local"`` (default) | ``"ssh"``
- When ``client="ssh"``, **required**: ``ssh_host``, ``ssh_port``, ``ssh_user``
  Optional auth: ``ssh_password`` or private-key path ``ssh_key``
- Unknown SSH host keys are rejected unless
  ``ssh_accept_unknown_host_key=True`` is explicitly supplied
"""

from __future__ import annotations

from typing import Mapping

from ..client import get_client as _get_client
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
    return _get_client(
        client=client,
        cwd=cwd,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key=ssh_key,
        ssh_flags=ssh_flags,
        accept_unknown_host_key=ssh_accept_unknown_host_key,
    )


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
    """Read a file from the local or remote filesystem.

    Reads up to ``limit`` lines (default 2000) starting from ``offset``.
    Results are returned with ``cat -n``-style line number prefixes when
    ``show_line_numbers`` is True. For large files, use ``offset`` and
    ``limit`` to bound the returned text and avoid overwhelming the context
    window. The backend still reads the complete file before selecting the
    requested line window.

    ``offset`` is 1-based: ``offset=1`` starts at the first line, and
    ``offset=0`` is treated as line 1. A negative ``offset`` (e.g. ``-50``)
    returns the last N lines of the file, ignoring ``limit`` for the tail
    window.

    The target must be a non-empty regular file. Missing files, directories,
    empty files, and other non-regular paths raise an error. UTF-8 decoding
    replaces invalid byte sequences.

    Args:
        target_file: Absolute path, or relative path resolved against
            ``cwd``.
        cwd: Working directory for resolving relative paths. Required.
        offset: 1-based starting line number. 0 is treated as 1. Negative
            values tail the last N lines. Defaults to 1.
        limit: Maximum lines to return for a non-negative offset. Must be
            > 0. Defaults to 2000.
        show_line_numbers: Prefix each line with its line number (``cat -n``
            format) and append a truncation notice when applicable. Set to
            False to return exact source text. Defaults to True.
        client: ``"local"`` (default) to read from the local filesystem,
            or ``"ssh"`` to read via SSH.
        ssh_host: SSH target hostname or IP (required when
            ``client="ssh"``).
        ssh_port: SSH port. Optional and ignored for ``client="local"``;
            required as a positive integer for ``client="ssh"``. No port
            defaults to 22.
        ssh_user: SSH login user (required when ``client="ssh"``).
        ssh_password: Optional explicit SSH password.
        ssh_key: Optional private-key file path on the host filesystem.
        ssh_flags: Space-separated supported OpenSSH-style options:
            ``-X``, ``-Y``, ``-A``, ``-a``, and ``-C``. Other options are
            ignored rather than passed to an OpenSSH process.
        ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
            missing from the host account's ``known_hosts`` file.

    Returns:
        The file content as a string, with optional line number prefixes
        and a truncation notice when the file exceeds the read window.

    Raises:
        ValueError: If ``cwd`` is empty, ``limit`` is not positive, the
            client name is invalid, or required SSH settings are absent.
        ReadFileNotFoundError: If the target file does not exist.
        ReadIsDirectoryError: If the target path is a directory.
        ReadEmptyFileError: If the file exists but contains no content.
        ReadError: If the path is not a regular file or cannot be read.
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
    """Create a new file or completely overwrite an existing file.

    Writes ``content`` to ``file_path``, creating missing parent directories
    automatically. If the target already exists and is a regular file, it is
    replaced in full — the previous contents are lost. The content is written
    exactly as supplied: no trailing newline is added automatically, and an
    empty string creates or truncates the file to zero bytes.

    Args:
        file_path: Absolute path, or relative path resolved against
            ``cwd``. Parent directories are created if missing.
        content: The complete text to write. Written as-is; add a trailing
            newline explicitly if one is desired.
        cwd: Working directory for resolving relative paths. Required.
        client: ``"local"`` (default) to write to the local filesystem,
            or ``"ssh"`` to write via SSH.
        ssh_host: SSH target hostname or IP (required when
            ``client="ssh"``).
        ssh_port: SSH port. Optional and ignored for ``client="local"``;
            required as a positive integer for ``client="ssh"``. No port
            defaults to 22.
        ssh_user: SSH login user (required when ``client="ssh"``).
        ssh_password: Optional explicit SSH password.
        ssh_key: Optional private-key file path on the host filesystem.
        ssh_flags: Space-separated supported OpenSSH-style options:
            ``-X``, ``-Y``, ``-A``, ``-a``, and ``-C``. Other options are
            ignored rather than passed to an OpenSSH process.
        ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
            missing from the host account's ``known_hosts`` file.

    Returns:
        A summary string with the number of bytes written and the resolved
        file path.

    Raises:
        ValueError: If ``cwd`` is empty, the client name is invalid, or
            required SSH settings are absent.
        WriteIsDirectoryError: If the destination is an existing directory.
        WriteError: If encoding, directory creation, or writing fails.

    Warning:
        Writing an existing file irreversibly replaces its contents.
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
    """Perform exact string replacement in a file.

    Searches for ``old_string`` in the target file and replaces it with
    ``new_string``.

    **Matching rules**:

    1. First attempts an **exact substring match** against the file content.
       ``old_string`` must match character-for-character, including
       indentation (tabs/spaces), blank lines, and trailing whitespace.
    2. If the exact match fails, retries with **per-line rstrip** tolerance:
       trailing whitespace is ignored independently on each line of both
       ``old_string`` and the file content. This makes matching robust
       against invisible trailing spaces without requiring the caller to
       manually strip them.
    3. By default, ``old_string`` must match **exactly once**. Multiple
       matches raise an error with context snippets to help disambiguate.
       Set ``replace_all=True`` to replace every non-overlapping occurrence.

    ``old_string`` must contain file text only. Line-number prefixes and
    truncation notices are display metadata and do not match file content.

    ``old_string=""`` means create a new file and its parent directories.
    Creation fails if the target already exists; it never overwrites or
    silently prepends. To prepend ``new_string`` to an existing file, also set
    ``prepend=True``. Prepend fails if the target does not exist.
    ``append`` is not provided by this tool. To append lines, use
    ``apply_patch`` with an ``Update File`` hunk containing a bare ``@@`` and
    only ``+`` lines; an add-only chunk without named context inserts at EOF.

    Like ``write``, ``edit`` never adds a trailing newline automatically. A
    normal replacement preserves the existing EOF newline unless the matched
    or replacement text explicitly changes it.

    Args:
        file_path: Absolute path, or relative path resolved against
            ``cwd``.
        old_string: The literal text to find and replace. Must match exactly
            (or match after per-line rstrip). An empty string means "create a
            new file". Include full indentation and surrounding context for
            uniqueness.
        new_string: Literal replacement text. When ``old_string`` is empty,
            this is the complete new-file content or, with ``prepend=True``,
            the content to prepend. Otherwise, an empty string deletes the
            matched text. It may equal a non-empty ``old_string``; such a call
            still counts and rewrites the selected match.
        cwd: Working directory for resolving relative paths. Required.
        replace_all: If True, replace every non-overlapping occurrence of
            ``old_string`` instead of requiring a single unique match.
            Defaults to False.
        prepend: If True, explicitly prepend ``new_string`` to an existing
            file. Requires ``old_string=""``. Defaults to False.
        client: ``"local"`` (default) to edit on the local filesystem,
            or ``"ssh"`` to edit via SSH.
        ssh_host: SSH target hostname or IP (required when
            ``client="ssh"``).
        ssh_port: SSH port. Optional and ignored for ``client="local"``;
            required as a positive integer for ``client="ssh"``. No port
            defaults to 22.
        ssh_user: SSH login user (required when ``client="ssh"``).
        ssh_password: Optional explicit SSH password.
        ssh_key: Optional private-key file path on the host filesystem.
        ssh_flags: Space-separated supported OpenSSH-style options:
            ``-X``, ``-Y``, ``-A``, ``-a``, and ``-C``. Other options are
            ignored rather than passed to an OpenSSH process.
        ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
            missing from the host account's ``known_hosts`` file.

    Returns:
        A summary string that explicitly reports ``created``, ``prepended``,
        or ``replaced`` and the resolved path.

    Raises:
        ValueError: If ``cwd`` is empty, the client name is invalid, or
            required SSH settings are absent; also if ``prepend=True`` is
            combined with a non-empty ``old_string`` or ``replace_all=True``.
        EditFileNotFoundError: If the target file does not exist and
            ``old_string`` is non-empty, create-on-empty is disabled, or an
            explicit prepend targets a missing file.
        EditFileExistsError: If ``old_string=""`` targets an existing file
            without ``prepend=True``.
        EditStringNotFoundError: If ``old_string`` cannot be found anywhere
            in the file.
        EditAmbiguousMatchError: If ``old_string`` appears multiple times
            and ``replace_all`` is False. The error message includes
            context snippets around each match to help disambiguate.
        EditError: If the path is a directory, not a regular file, or an
            I/O error occurs.
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
    """Apply a structured patch to one or more text files.

    The patch may add, update, delete, or move files. Paths are absolute or
    resolved against ``cwd`` on the selected client. The complete document is
    parsed and preflighted against an in-memory filesystem view before writes
    begin. Syntax errors, missing sources, conflicting destinations, and
    unmatched update context prevent any filesystem mutation. Low-level
    failures during the write/delete phase cannot be rolled back
    transactionally.

    Pass the complete patch as the string value of ``patch_text``. The string
    must not contain Markdown fences or a second JSON wrapper. A surrounding
    shell heredoc is accepted but unnecessary. The format is not a standard
    unified diff: omit ``diff --git``, ``---``, ``+++``, and numeric
    hunk-range headers.

    Every control marker (``***`` and ``@@``) must start in column 1. Inside
    ``Update File``, every content line also needs a one-character prefix:
    space for unchanged context, ``-`` for removal, or ``+`` for addition. A
    blank content line still needs one of these prefixes.
    The Markdown fences around the examples below are documentation delimiters
    only; pass only the lines between them as ``patch_text``.

    Replacement with unchanged context:

    ```text
    *** Begin Patch
    *** Update File: settings.txt
    @@
     [theme]
    -color=blue
    +color=green
    *** End Patch
    ```

    The character before ``[theme]`` above is the required space prefix; it is
    not indentation from the docstring.

    Pure insertion after a unique line:

    ```text
    *** Begin Patch
    *** Update File: app.py
    @@ def main():
    +    log_startup()
    *** End Patch
    ```

    EOF-anchored replacement:

    ```text
    *** Begin Patch
    *** Update File: app.py
    @@
    -raise SystemExit(main())
    +raise SystemExit(run())
    *** End of File
    *** End Patch
    ```

    Move without changing contents:

    ```text
    *** Begin Patch
    *** Update File: old_name.py
    *** Move to: new_name.py
    *** End Patch
    ```

    A patch may contain multiple file hunks. To add a nonexistent file, use
    ``*** Add File: path`` followed only by ``+``-prefixed content lines. To
    delete an existing regular file, use ``*** Delete File: path`` with no
    content lines. ``*** Update File: path`` requires an existing regular file.

    ``@@`` starts an update chunk; it never takes unified-diff line numbers.
    ``@@ <context>`` seeks to a literal full line in the file, then starts the
    chunk after that line. A chunk containing only ``+`` lines inserts there;
    without named context, an add-only chunk inserts at EOF. Put
    ``*** End of File`` after a chunk to require its old lines to match at EOF.
    Copy match text from the file when possible. Matching tries exact lines
    first, then tolerates trailing whitespace, full trim, and normalized
    Unicode punctuation/spaces. Chunks run in order and later searches start
    after earlier matches.

    ``*** Move to: destination`` is valid only inside ``Update File`` and may
    stand alone or accompany edits; the destination must not exist.
    ``*** End Patch`` is the final control line. Added files and non-empty
    content updates end with a newline; empty results remain zero bytes, while
    move-only hunks preserve the source bytes.

    Args:
        patch_text: Complete patch document including ``*** Begin Patch`` and
            ``*** End Patch``.
        cwd: Required working directory on the selected client. Relative patch
            paths are resolved from here.
        client: ``"local"`` (default) or ``"ssh"``.
        ssh_host: SSH hostname or IP, required for ``client="ssh"``.
        ssh_port: SSH port. Optional and ignored for ``client="local"``;
            required as a positive integer for ``client="ssh"``. No port
            defaults to 22.
        ssh_user: SSH login user, required for ``client="ssh"``.
        ssh_password: Optional explicit SSH password.
        ssh_key: Optional private-key file path on the host filesystem.
        ssh_flags: Space-separated supported OpenSSH-style options:
            ``-X``, ``-Y``, ``-A``, ``-a``, and ``-C``. Other options are
            ignored rather than passed to an OpenSSH process.
        ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
            missing from the host account's ``known_hosts`` file.

    Returns:
        A summary of patch-relative paths grouped by operation, for example
        ``"added=['new.txt'] modified=['app.py'] deleted=['old.txt']"``.
        Moves are reported under ``modified`` using their destination path.

    Raises:
        ValueError: If ``cwd`` is empty, the client name is invalid, or
            required SSH settings are absent.
        PatchParseError: If the patch grammar is invalid or incomplete.
        PatchSeekError: If an update chunk cannot be located in its file.
        PatchApplyError: If a path precondition fails or filesystem I/O fails.

    Warning:
        Delete and move hunks remove source files after destination writes
        complete.
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
    """Execute a shell command and return its output.

    Runs ``command`` via the selected interpreter in the explicit ``cwd``.
    By default this means ``cmd /c <command>`` for local Windows and
    ``bash -c <command>`` for Linux, macOS, and SSH. The
    command string is passed through **without content filtering** — pipes,
    redirects, shell expansions, ``&&``/``||`` chaining, subshells, and
    background operators (``&``) all work as they would in an interactive
    shell.

    The returned summary contains the exit code, working directory, wall-clock
    duration, command, stdout, and stderr when present. Formatted output is
    capped at 100,000 characters; when exceeded, both the beginning and end are
    preserved with a truncation marker. Raw stream capture is limited to
    ``max_output_bytes`` independently for stdout and stderr.

    The timeout defaults to 120 seconds; zero disables it. A timed-out command
    returns exit code 124, the ``timed_out`` marker, and captured partial output
    instead of raising a timeout exception.

    ``cwd`` is required on every call and applies only to that invocation.

    Execution is foreground and non-interactive. No reusable terminal session
    or PTY is created. Background operators are interpreted normally, but no
    background process lifecycle is managed after the call returns.

    Known interpreters receive their command-string flag automatically:
    ``-c`` for POSIX shells, Python, Ruby, and Perl; ``-Command`` for
    PowerShell; and ``/c`` for ``cmd``. ``flags`` contains only additional
    interpreter options.

    ``env`` overlays variables for the invocation without modifying the parent
    environment. ``stdin`` supplies literal UTF-8 text and is then closed;
    omitting it sends immediate EOF.

    Args:
        command: The shell command to execute. Non-empty string. Supports
            multiline scripts, pipes, redirects, and shell operators.
        cwd: Working directory for the command. Required.
        timeout: Maximum seconds to wait. 0 disables the timeout. Negative
            values are rejected. Defaults to 120.
        description: A short human-readable label for the command (appears
            in the output summary). Useful for documenting intent.
        interpreter: Shell or runtime to use. Defaults to ``"auto"``, which
            selects ``cmd`` on local Windows and ``bash`` elsewhere.
        flags: Additional interpreter flags (e.g. ``"-euxo pipefail"``).
            Parsing follows Windows rules for local Windows and POSIX rules
            elsewhere. Do not include the command-string flag because it is
            injected automatically.
        env: Environment-variable overrides for this invocation. Keys and
            values are converted to strings; names must use the portable
            ``[A-Za-z_][A-Za-z0-9_]*`` form and values cannot contain NUL.
        stdin: Text to pipe to the command's standard input.
        max_output_bytes: Positive per-stream retained-output limit in bytes.
            Defaults to 1 MiB (1048576) and may not exceed 16 MiB (16777216).
        client: ``"local"`` (default) to run on the local machine, or
            ``"ssh"`` to run via SSH.
        ssh_host: SSH target hostname or IP (required when
            ``client="ssh"``).
        ssh_port: SSH port. Optional and ignored for ``client="local"``;
            required as a positive integer for ``client="ssh"``. No port
            defaults to 22.
        ssh_user: SSH login user (required when ``client="ssh"``).
        ssh_password: Optional explicit SSH password.
        ssh_key: Optional private-key file path on the host filesystem.
        ssh_flags: Space-separated supported OpenSSH-style options:
            ``-X``, ``-Y``, ``-A``, ``-a``, and ``-C``. Other options are
            ignored rather than passed to an OpenSSH process.
        ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
            missing from the host account's ``known_hosts`` file.

    Returns:
        A formatted string with exit code, cwd, duration, the original
        command, stdout, and stderr (if any). Timed-out commands include
        partial output. Exceeding the 100,000-character display cap
        preserves both head and tail with a truncation marker.

    Raises:
        ValueError: If ``cwd`` is empty, the client name is invalid, or
            required SSH settings are absent.
        BashError: If the command, timeout, interpreter, flags, environment,
            or output limit is invalid, or execution cannot be started.

    Warning:
        No sandbox, approval gate, or command filtering is applied. Commands
        run with the permissions of the selected client.
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
