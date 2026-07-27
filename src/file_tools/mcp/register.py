"""Register the public file tools with an MCP server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from . import tools as impl


_Tool = TypeVar("_Tool", bound=Callable[..., Awaitable[str]])


class _ToolRegistrar(Protocol):
    def tool(self, function: _Tool, /) -> _Tool: ...


def register_tools(mcp: _ToolRegistrar) -> None:
    """Register the five public tools on ``mcp``."""

    @mcp.tool
    async def read(
        target_file: str,
        cwd: str,
        offset: int = 1,
        limit: int = 2000,
        show_line_numbers: bool = True,
        connection: str = "local",
    ) -> str:
        """Read a UTF-8 text file from a local or SSH-backed filesystem.

        Relative paths are resolved against ``cwd`` on the selected client.
        The target must exist and must be a non-empty regular file; directories
        and other non-regular paths are rejected. UTF-8 decoding replaces
        invalid byte sequences.

        Line selection follows these rules:

        - ``offset >= 1`` starts at that 1-based line number.
        - ``offset == 0`` is accepted as an alias for line 1.
        - ``offset < 0`` returns the last ``abs(offset)`` lines and ignores
          ``limit`` when choosing the window.
        - A positive ``offset`` beyond end-of-file returns an empty string.
        - ``limit`` must be greater than zero. It caps the number of returned
          lines only when ``offset`` is non-negative.

        When ``show_line_numbers`` is true, each returned line is prefixed in
        ``cat -n`` style. If a positive-offset read is truncated by ``limit``,
        the result also ends with a truncation notice. Continue with a larger
        positive ``offset`` to inspect the next window. Set line numbers to
        false to return exact file text because the prefixes and truncation
        notice are display metadata, not file content.

        Args:
            target_file: File to read. May be absolute, ``~``-relative, or
                relative to ``cwd``.
            cwd: Required working directory on the selected client. Relative
                file paths are resolved from here; never rely on an implicit
                process working directory.
            offset: 1-based starting line, or a negative tail count. Defaults
                to 1.
            limit: Maximum lines for a non-negative offset. Must be positive;
                defaults to 2000.
            show_line_numbers: Add line-number prefixes and a truncation notice
                when applicable. Defaults to true.
            connection: ``"local"`` or a named profile loaded from
                ``FILE_TOOLS_CONNECTIONS_FILE``. Defaults to ``"local"``.

        Returns:
            The selected text, optionally line-numbered. UTF-8 decoding errors
            are replaced rather than raised.

        Raises:
            ValueError: If ``cwd`` is empty, ``limit`` is not positive, or the
                named connection profile is missing or invalid.
            ReadError: If the path is missing, empty, not a regular file, or
                cannot be read.
        """
        return await impl.read(
            target_file,
            cwd,
            offset=offset,
            limit=limit,
            show_line_numbers=show_line_numbers,
            connection=connection,
        )

    @mcp.tool
    async def write(
        file_path: str,
        content: str,
        cwd: str,
        connection: str = "local",
    ) -> str:
        """Create or completely overwrite a UTF-8 text file.

        Missing parent directories are created automatically. If ``file_path``
        already names a regular file, all existing contents are replaced
        without a read-before-write check or backup.

        The ``content`` argument is written exactly as supplied: no trailing
        newline is added and an empty string creates or truncates the target to
        a zero-byte file. The target cannot be an existing directory. This is
        a text tool rather than a binary-file API; ``content`` is encoded as
        UTF-8 before writing. Read an existing file first whenever any of its
        current contents must be preserved.

        Args:
            file_path: Destination file. May be absolute, ``~``-relative, or
                relative to ``cwd``.
            content: Complete replacement contents encoded as UTF-8.
            cwd: Required working directory on the selected client. Relative
                file paths are resolved from here.
            connection: ``"local"`` or a named profile loaded from
                ``FILE_TOOLS_CONNECTIONS_FILE``. Defaults to ``"local"``.

        Returns:
            A confirmation string containing the number of UTF-8 bytes written
            and the resolved destination path, for example
            ``"wrote 6 bytes to /workspace/note.txt"``.

        Raises:
            ValueError: If ``cwd`` is empty or the named connection profile is
                missing or invalid.
            WriteError: If the destination is a directory or the file cannot be
                encoded, created, or overwritten.

        Warning:
            This operation is destructive for an existing file and does not
            preserve its previous contents.
        """
        return await impl.write(
            file_path,
            content,
            cwd,
            connection=connection,
        )

    @mcp.tool
    async def edit(
        file_path: str,
        old_string: str,
        new_string: str,
        cwd: str,
        replace_all: bool = False,
        prepend: bool = False,
        connection: str = "local",
    ) -> str:
        """Replace a specific string in a local or remote UTF-8 text file.

        Matching first searches for exact substring occurrences. If there are
        none, it retries with trailing whitespace ignored independently on each
        line.

        By default, ``old_string`` must match exactly once. No match raises an
        error; multiple matches raise an ambiguity error instead of guessing.
        Include more surrounding text to make the match unique, or set
        ``replace_all=true`` only when every occurrence should change.
        ``old_string`` must contain file text only; line-number prefixes and
        truncation notices are display metadata and will not match.

        ``old_string=""`` means create a new file. Missing parent directories
        are created as needed, but an existing target raises an error instead
        of being overwritten or silently prepended. Prepending is explicit:
        also set ``prepend=true``; the target must already exist.
        ``append`` is not provided by this tool. To append lines, use
        ``apply_patch`` with an ``Update File`` hunk containing a bare ``@@``
        and only ``+`` lines; an add-only chunk without named context inserts
        at EOF.

        Like ``write``, ``edit`` never adds a trailing newline automatically.
        A normal replacement preserves the existing EOF newline unless the
        matched or replacement text explicitly changes it.

        Args:
            file_path: File to edit. May be absolute, ``~``-relative, or
                relative to ``cwd``.
            old_string: Literal text to replace. Preserve indentation and line
                breaks. An empty string means create a new file, not a normal
                zero-width replacement.
            new_string: Literal replacement text. When ``old_string`` is empty,
                this is the complete new-file content or, with
                ``prepend=true``, the content to prepend. Otherwise, an empty
                string deletes the matched text.
            cwd: Required working directory on the selected client. Relative
                file paths are resolved from here.
            replace_all: Replace every non-overlapping match. When false, the
                match must be unique. Defaults to false.
            prepend: Explicitly prepend ``new_string`` to an existing file.
                Requires ``old_string=""``. Defaults to false.
            connection: ``"local"`` or a named profile loaded from
                ``FILE_TOOLS_CONNECTIONS_FILE``. Defaults to ``"local"``.

        Returns:
            A confirmation that explicitly distinguishes the operation, for
            example ``"created /workspace/new.py"``,
            ``"prepended to /workspace/app.py"``, or
            ``"replaced /workspace/app.py (1 matches)"``.

        Raises:
            ValueError: If ``cwd`` is empty, the named connection profile is
                invalid, or if ``prepend=true`` is
                combined with a non-empty ``old_string`` or
                ``replace_all=true``.
            EditFileNotFoundError: If the file is missing and ``old_string`` is
                non-empty, or an explicit prepend targets a missing file.
            EditFileExistsError: If create-on-empty targets an existing file.
            EditStringNotFoundError: If ``old_string`` does not match.
            EditAmbiguousMatchError: If it matches more than once while
                ``replace_all`` is false.
            EditError: If the path is not a regular file or I/O fails.
        """
        return await impl.edit(
            file_path,
            old_string,
            new_string,
            cwd,
            replace_all=replace_all,
            prepend=prepend,
            connection=connection,
        )

    @mcp.tool
    async def apply_patch(
        patch_text: str,
        cwd: str,
        connection: str = "local",
    ) -> str:
        """Apply a structured patch to one or more text files.

        The patch may add, update, delete, or move files. Paths are absolute or
        resolved against ``cwd`` on the selected client. The complete document
        is parsed and preflighted against an in-memory filesystem view before
        writes begin. Syntax errors, missing sources, conflicting destinations,
        and unmatched update context prevent any filesystem mutation. Low-level
        Commit failures trigger a best-effort deterministic rollback. Every
        write and delete also verifies the staged file version.

        ``patch_text`` contains the complete patch document directly, without
        Markdown fences or a serialized JSON wrapper. Content lines must not
        have extra indentation. A surrounding shell heredoc is accepted but
        unnecessary. The format is not a standard unified diff: omit
        ``diff --git``, ``---``, ``+++``, and numeric hunk-range headers.

        Every control marker (``***`` and ``@@``) must start in column 1.
        Inside ``Update File``, every content line also needs a one-character
        prefix: space for unchanged context, ``-`` for removal, or ``+`` for
        addition. A blank content line still needs one of these prefixes.
        The Markdown fences around the examples below are documentation
        delimiters only; pass only the lines between them as ``patch_text``.

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

        The character before ``[theme]`` above is the required space prefix;
        it is not indentation from the code block.

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
        ``*** Add File: path`` followed only by ``+``-prefixed content lines.
        To delete an existing regular file, use ``*** Delete File: path`` with
        no content lines. ``*** Update File: path`` requires an existing
        regular file.

        ``@@`` starts an update chunk; it never takes unified-diff line
        numbers. ``@@ <context>`` seeks to a literal full line in the file,
        then starts the chunk after that line. A chunk containing only ``+``
        lines inserts there; without named context, an add-only chunk inserts
        at EOF. Put ``*** End of File`` after a chunk to require its old lines
        to match at EOF. Copy match text from the file when possible. Matching
        tries exact lines first, then tolerates trailing whitespace, full trim,
        and normalized Unicode punctuation/spaces. Chunks run in order and
        later searches start after earlier matches.

        ``*** Move to: destination`` is valid only inside ``Update File`` and
        may stand alone or accompany edits; the destination must not exist.
        ``*** End Patch`` is the final control line. Added files and non-empty
        content updates end with a newline; empty results remain zero bytes,
        while move-only hunks preserve the source bytes.

        Args:
            patch_text: Full patch document, including ``*** Begin Patch`` and
                ``*** End Patch``. Supply it directly as text, without Markdown
                fences. A surrounding ``<<EOF`` heredoc wrapper is accepted but
                unnecessary.
            cwd: Required working directory on the selected client. Relative
                patch paths are resolved from here.
            connection: ``"local"`` or a named profile loaded from
                ``FILE_TOOLS_CONNECTIONS_FILE``. Defaults to ``"local"``.

        Returns:
            A summary of patch-relative paths grouped by operation, for example
            ``"added=['new.txt'] modified=['app.py'] deleted=['old.txt']"``.
            A moved file is reported under ``modified`` using its destination.

        Raises:
            ValueError: If ``cwd`` is empty or the named connection profile is
                missing or invalid.
            PatchParseError: If the patch grammar is invalid or incomplete.
            PatchSeekError: If an update chunk cannot be located in its file.
            PatchApplyError: If a required source is missing, a destination
                already exists, a path is not a regular file, or I/O fails.

        Warning:
            Delete and move hunks remove source files after destination writes
            complete.
        """
        return await impl.apply_patch(
            patch_text,
            cwd,
            connection=connection,
        )

    @mcp.tool
    async def bash(
        command: str,
        cwd: str,
        timeout: float = 120.0,
        description: str = "",
        interpreter: str = "auto",
        flags: str = "",
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        max_output_bytes: int = 1024 * 1024,
        connection: str = "local",
    ) -> str:
        """Execute a foreground command through a selected shell.

        By default, ``interpreter="auto"`` selects ``cmd`` for local Windows
        and ``bash`` for local Linux/macOS or SSH, then executes the supplied
        string in ``cwd``.

        The command string is passed to the interpreter without content
        filtering. Shell syntax such as pipes, redirects, variable expansion,
        command substitution, ``&&``, and background operators therefore has
        its normal meaning. No sandbox, approval gate, command filtering,
        background-task manager, or automatic safety policy is applied.
        Execution is foreground and non-interactive: the call returns after the
        process exits or times out, and no reusable terminal session or PTY is
        exposed for later input.

        Known interpreters automatically receive their command-string option:
        ``-c`` for POSIX shells/Python/Ruby/Perl, ``-Command`` for PowerShell,
        and ``/c`` for ``cmd``. Supply only additional interpreter options in
        ``flags``; for example, ``interpreter="bash", flags="-l"`` becomes a
        login-shell invocation with the required ``-c`` injected.

        Args:
            command: Non-empty shell script or command string. It may contain
                multiple lines and shell operators.
            cwd: Required working directory on the selected client. The command
                runs here; it is never inferred implicitly.
            timeout: Maximum foreground execution time in seconds. Defaults to
                120. ``0`` disables the timeout. Negative, NaN, and infinite
                values are rejected. A timeout returns a normal result with
                exit code 124 and ``timed_out`` in its header.
            description: Optional short purpose included as ``desc: ...`` in
                the result header. It does not affect execution.
            interpreter: Executable name or path used to evaluate ``command``.
                Defaults to ``auto`` (``cmd`` on local Windows, ``bash``
                elsewhere).
            flags: Additional interpreter flags, parsed with Windows rules for
                local Windows and POSIX rules elsewhere. Usually omit the
                command-string flag because it is injected automatically.
            env: Optional environment-variable overrides for the child process.
                Keys and values are converted to strings. Names must match
                ``[A-Za-z_][A-Za-z0-9_]*`` and values cannot contain NUL;
                remote values are shell-quoted rather than interpolated.
            stdin: Optional UTF-8 text sent to the command before stdin is
                closed. Omit it for an immediate EOF.
            max_output_bytes: Maximum bytes retained independently for stdout
                and stderr while excess bytes continue to be drained. Must be a
                positive integer no greater than 16 MiB; defaults to 1 MiB.
            connection: ``"local"`` or a named profile loaded from
                ``FILE_TOOLS_CONNECTIONS_FILE``. Defaults to ``"local"``.

        Returns:
            A bounded text report containing ``exit``, resolved ``cwd``,
            duration, the original command, stdout, and a labeled stderr
            section when present. Non-zero exits are returned rather than
            raised. Capture is bounded while the command runs; formatting adds
            a 100,000-character cap and preserves both the beginning and end.

        Raises:
            ValueError: If ``cwd`` is empty or the named connection profile is
                missing or invalid.
            BashError: If the command, timeout, interpreter, flags,
                environment, or output limit is invalid, or if the process/SSH
                command cannot be started.

        Warning:
            Commands can modify or delete data and execute arbitrary programs
            with the selected client's permissions.
        """
        return await impl.bash(
            command,
            cwd,
            timeout=timeout,
            description=description,
            interpreter=interpreter,
            flags=flags,
            env=env,
            stdin=stdin,
            max_output_bytes=max_output_bytes,
            connection=connection,
        )
