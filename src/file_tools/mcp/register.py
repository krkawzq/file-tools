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

        Use for inspecting source, configs, logs, and other text before edits.
        Always pass an explicit ``cwd``; relative paths resolve against that
        workspace on the selected client. Prefer small windows over whole-file
        dumps so the context stays bounded.

        The target must be a non-empty regular file. Missing paths,
        directories, empty files, and other non-regular paths raise. UTF-8
        decoding replaces invalid byte sequences. Path resolution follows a
        symbolic link when its resolved target is a regular file; FIFOs,
        devices, and other special files are rejected before opening.

        The selected text window is limited to 16 MiB of transferred bytes;
        this MCP surface does not expose a larger transfer setting. SSH
        connection and individual file operations use 30-second timeouts.

        **Line selection**

        - ``offset >= 1`` starts at that 1-based line (default ``1``).
        - ``offset == 0`` is treated as line 1.
        - ``offset < 0`` returns the last ``abs(offset)`` lines and ignores
          ``limit`` for the window size.
        - A positive ``offset`` past EOF returns an empty string.
        - ``limit`` must be ``> 0`` (default ``2000``) and only caps
          non-negative-offset reads.

        When ``show_line_numbers`` is true (default), lines use ``cat -n``
        prefixes and a truncation notice may be appended if ``limit`` cut the
        window short. Continue from the next 1-based line after ``end`` when
        truncated. Set ``show_line_numbers=false`` when you need exact file
        text for a later ``edit`` or comparison — prefixes and notices are
        display metadata, not file content.

        Args:
            target_file: File to read. Absolute, ``~``-relative, or relative
                to ``cwd``.
            cwd: Required working directory on the selected client.
            offset: 1-based start line, or negative tail count. Defaults to 1.
            limit: Max lines for a non-negative offset. Must be positive;
                defaults to 2000.
            show_line_numbers: Prefix lines and append a truncation notice
                when applicable. Defaults to true.
            client: ``"local"`` (default) or ``"ssh"``. SSH parameters are
                ignored for local.
            ssh_host: Hostname, IP, or ``Host`` alias from ``~/.ssh/config``.
                Required when ``client="ssh"``.
            ssh_port: Positive SSH port when ``client="ssh"``. No implicit 22.
            ssh_user: SSH username when ``client="ssh"``.
            ssh_password: Optional explicit password (prefer keys/agent).
            ssh_key: Optional private-key path on the host filesystem.
            ssh_flags: Space-separated supported OpenSSH flags: ``-X``,
                ``-Y``, ``-A``, ``-a``, ``-C``. Others are ignored.
            ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
                missing from ``known_hosts``.

        Returns:
            The selected text, optionally line-numbered, with a truncation
            notice when a positive-offset window is cut short by ``limit``.

        Raises:
            ValueError: Empty ``cwd``, non-positive ``limit``, invalid client,
                or missing SSH settings.
            ReadError: Missing, empty, non-regular, or unreadable path.
        """
        return await impl.read(
            target_file,
            cwd,
            offset=offset,
            limit=limit,
            show_line_numbers=show_line_numbers,
            client=client,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key=ssh_key,
            ssh_flags=ssh_flags,
            ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
        )

    @mcp.tool
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

        Use only when the intended result is the full new file body. Prefer
        ``edit`` for localized changes and ``apply_patch`` for multi-file or
        structured updates. Always pass explicit ``cwd``.

        Missing parents are created. An existing regular file is overwritten
        with no backup and no merge. ``content`` is written exactly as given:
        no trailing newline is added; an empty string yields a zero-byte file.
        Existing directories cannot be targets. This is a text API (UTF-8
        encode on write), not a binary blob store.

        The encoded payload is limited to 16 MiB; this MCP surface does not
        expose a larger transfer setting. SSH connection and individual file
        operations use 30-second timeouts.

        Args:
            file_path: Destination. Absolute, ``~``-relative, or relative to
                ``cwd``.
            content: Complete file body to write (UTF-8).
            cwd: Required working directory on the selected client.
            client: ``"local"`` (default) or ``"ssh"``. SSH parameters are
                ignored for local.
            ssh_host: Hostname, IP, or ``Host`` alias from ``~/.ssh/config``.
                Required when ``client="ssh"``.
            ssh_port: Positive SSH port when ``client="ssh"``. No implicit 22.
            ssh_user: SSH username when ``client="ssh"``.
            ssh_password: Optional explicit password (prefer keys/agent).
            ssh_key: Optional private-key path on the host filesystem.
            ssh_flags: Space-separated supported OpenSSH flags: ``-X``,
                ``-Y``, ``-A``, ``-a``, ``-C``. Others are ignored.
            ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
                missing from ``known_hosts``.

        Returns:
            Confirmation with byte count and resolved path, e.g.
            ``"wrote 6 bytes to /workspace/note.txt"``.

        Raises:
            ValueError: Empty ``cwd``, invalid client, or missing SSH settings.
            WriteError: Destination is a directory, or encode/create/write
                fails.

        Warning:
            Destructive for existing files: previous contents are discarded.
        """
        return await impl.write(
            file_path,
            content,
            cwd,
            client=client,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key=ssh_key,
            ssh_flags=ssh_flags,
            ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
        )

    @mcp.tool
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

        Prefer this for a single-file, uniqueness-checked change. Use
        ``apply_patch`` for multi-file, move/delete, or EOF append. Always
        pass explicit ``cwd``. Read with ``show_line_numbers=false`` before
        matching so prefixes are not copied into ``old_string``.

        **Modes**

        - **Replace** (default): ``old_string`` must match file text. Exact
          substring first; if none, retry with per-line trailing-whitespace
          tolerance. Default requires exactly one match; zero matches error,
          multiple matches error unless ``replace_all=true``.
        - **Create**: ``old_string=""`` creates a new file (parents as
          needed). Existing paths raise — never silent overwrite or prepend.
        - **Prepend**: ``old_string=""`` and ``prepend=true`` insert
          ``new_string`` at the start of an existing file.

        There is no append mode. To append at EOF, use ``apply_patch`` with an
        ``Update File`` hunk that has a bare ``@@`` and only ``+`` lines.

        ``old_string`` / ``new_string`` preserve literal indentation and text.
        When the target contains CRLF, LF line breaks in match/replacement or
        prepend text are accepted and written as CRLF to preserve the file's
        newline convention. Empty ``new_string`` on a replace deletes the
        match. No trailing newline is auto-added; a normal replace keeps the
        file's existing EOF newline state unless the match or replacement
        changes it.

        Each file read or write is limited to 16 MiB; this MCP surface does not
        expose a larger transfer setting. SSH connection and individual file
        operations use 30-second timeouts.

        Args:
            file_path: File to edit. Absolute, ``~``-relative, or relative to
                ``cwd``.
            old_string: Literal match text, or ``""`` for create/prepend.
            new_string: Replacement, new-file body, or prepend prefix. Empty
                on replace deletes the matched span. LF is normalized to CRLF
                when the existing target uses CRLF.
            cwd: Required working directory on the selected client.
            replace_all: Replace every non-overlapping match. Defaults false
                (unique match required).
            prepend: Prepend ``new_string`` to an existing file; requires
                ``old_string=""``. Defaults false.
            client: ``"local"`` (default) or ``"ssh"``. SSH parameters are
                ignored for local.
            ssh_host: Hostname, IP, or ``Host`` alias from ``~/.ssh/config``.
                Required when ``client="ssh"``.
            ssh_port: Positive SSH port when ``client="ssh"``. No implicit 22.
            ssh_user: SSH username when ``client="ssh"``.
            ssh_password: Optional explicit password (prefer keys/agent).
            ssh_key: Optional private-key path on the host filesystem.
            ssh_flags: Space-separated supported OpenSSH flags: ``-X``,
                ``-Y``, ``-A``, ``-a``, ``-C``. Others are ignored.
            ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
                missing from ``known_hosts``.

        Returns:
            Operation-tagged confirmation, e.g.
            ``"created /workspace/new.py"``,
            ``"prepended to /workspace/app.py"``, or
            ``"replaced /workspace/app.py (1 matches)"``.

        Raises:
            ValueError: Empty ``cwd``, invalid client/SSH settings, or
                ``prepend=true`` with non-empty ``old_string`` or
                ``replace_all=true``.
            EditFileNotFoundError: Missing file for replace or prepend.
            EditFileExistsError: Create-on-empty hits an existing path.
            EditStringNotFoundError: No match for ``old_string``.
            EditAmbiguousMatchError: Multiple matches while
                ``replace_all`` is false.
            EditError: Not a regular file or I/O failure.
        """
        return await impl.edit(
            file_path,
            old_string,
            new_string,
            cwd,
            replace_all=replace_all,
            prepend=prepend,
            client=client,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key=ssh_key,
            ssh_flags=ssh_flags,
            ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
        )

    @mcp.tool
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

        Use for coordinated changes across one or more paths, moves, deletes,
        or EOF appends. Prefer ``edit`` for a single unique literal replace.
        Always pass explicit ``cwd``; patch paths resolve against it.

        The full document is parsed and preflighted against an in-memory view
        before any write. Grammar errors, missing sources, conflicting
        destinations, unmatched context, and stale versions block all mutation.
        Low-level commit failures attempt best-effort deterministic rollback.
        Each file read or write is limited to 16 MiB; this MCP surface does not
        expose a larger transfer setting. SSH connection and individual file
        operations use 30-second timeouts.

        **Format (not unified diff)**

        Pass the patch body only — no Markdown fences, no ``diff --git`` /
        ``---`` / ``+++`` / numeric hunk headers. Every ``***`` and ``@@``
        marker starts in column 1. Inside ``Update File``, each content line
        needs a one-character prefix: space (context), ``-`` (remove), or
        ``+`` (add); blank content lines still need a prefix.

        The fenced blocks below are documentation only; pass the inner lines as
        ``patch_text``.

        Replace with context:

        ```text
        *** Begin Patch
        *** Update File: settings.txt
        @@
         [theme]
        -color=blue
        +color=green
        *** End Patch
        ```

        (The space before ``[theme]`` is the required context prefix.)

        Insert after a unique line:

        ```text
        *** Begin Patch
        *** Update File: app.py
        @@ def main():
        +    log_startup()
        *** End Patch
        ```

        EOF-anchored replace:

        ```text
        *** Begin Patch
        *** Update File: app.py
        @@
        -raise SystemExit(main())
        +raise SystemExit(run())
        *** End of File
        *** End Patch
        ```

        Append at EOF (bare ``@@``, only ``+`` lines):

        ```text
        *** Begin Patch
        *** Update File: notes.txt
        @@
        +trailing line
        *** End Patch
        ```

        Move only:

        ```text
        *** Begin Patch
        *** Update File: old_name.py
        *** Move to: new_name.py
        *** End Patch
        ```

        **Hunk rules**

        - ``*** Add File: path`` — target must not exist; body lines are
          ``+``-prefixed only.
        - ``*** Delete File: path`` — existing regular file; no content lines.
        - ``*** Update File: path`` — existing regular file.
        - ``@@`` starts a chunk (no line numbers). ``@@ <line>`` seeks that
          full line and applies after it; add-only without named context
          inserts at EOF.
        - ``*** End of File`` after a chunk requires the old lines to match at
          EOF.
        - Matching: exact lines first, then trailing-whitespace / trim /
          Unicode-normalized fallbacks. Chunks apply in order; later searches
          start after earlier matches.
        - ``*** Move to: dest`` only inside Update; destination must not exist.
        - Added / non-empty updates end with a newline; empty stays zero bytes;
          move-only preserves source text and newline shape for valid UTF-8.
          Invalid UTF-8 bytes are decoded with replacement before the move is
          committed, so this remains a text operation rather than a binary move.

        Args:
            patch_text: Full document from ``*** Begin Patch`` through
                ``*** End Patch``.
            cwd: Required working directory on the selected client.
            client: ``"local"`` (default) or ``"ssh"``. SSH parameters are
                ignored for local.
            ssh_host: Hostname, IP, or ``Host`` alias from ``~/.ssh/config``.
                Required when ``client="ssh"``.
            ssh_port: Positive SSH port when ``client="ssh"``. No implicit 22.
            ssh_user: SSH username when ``client="ssh"``.
            ssh_password: Optional explicit password (prefer keys/agent).
            ssh_key: Optional private-key path on the host filesystem.
            ssh_flags: Space-separated supported OpenSSH flags: ``-X``,
                ``-Y``, ``-A``, ``-a``, ``-C``. Others are ignored.
            ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
                missing from ``known_hosts``.

        Returns:
            Path summary by operation, e.g.
            ``"added=['new.txt'] modified=['app.py'] deleted=['old.txt']"``.
            Moves appear under ``modified`` at the destination path.

        Raises:
            ValueError: Empty ``cwd``, invalid client, or missing SSH settings.
            PatchParseError: Invalid or incomplete patch grammar.
            PatchSeekError: An update chunk cannot be located.
            PatchApplyError: Missing source, existing destination, non-regular
                path, or I/O failure.

        Warning:
            Delete and move remove source paths after destination writes
            succeed.
        """
        return await impl.apply_patch(
            patch_text,
            cwd,
            client=client,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key=ssh_key,
            ssh_flags=ssh_flags,
            ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
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

        Use for diagnostics, builds, tests, and other shell work that is not a
        structured file edit. Prefer ``read`` / ``edit`` / ``apply_patch`` /
        ``write`` for file mutations. Always pass explicit ``cwd``.

        Default ``interpreter="auto"`` selects ``cmd`` on local Windows and
        ``bash`` on local Unix or SSH. The command string is handed to the
        interpreter with full shell semantics (pipes, redirects, expansions,
        ``&&``, ``&``, …). There is no sandbox, approval gate, content filter,
        reusable shell, PTY, or background-task manager. The call returns when
        the process exits or times out.

        Known interpreters get their command-string flag injected: ``-c``
        (POSIX shells, Python, Ruby, Perl), ``-Command`` (PowerShell),
        ``/c`` (``cmd``). Put only extra options in ``flags`` (e.g.
        ``interpreter="bash", flags="-l"``).

        Args:
            command: Non-empty command or script (may be multiline).
            cwd: Required working directory on the selected client.
            timeout: Max seconds (default 120). ``0`` disables. Negative /
                NaN / inf rejected. On timeout: exit 124, ``timed_out`` in
                the report, partial output kept.
            description: Optional short label shown as ``desc: ...`` in the
                report header (does not affect execution).
            interpreter: Executable or ``auto`` (default).
            flags: Extra interpreter flags (platform-aware parsing). Usually
                omit the injected command-string flag.
            env: Optional env overrides. Names must match
                ``[A-Za-z_][A-Za-z0-9_]*``; values must not contain NUL.
            stdin: Optional UTF-8 text; then stdin is closed. Omit for
                immediate EOF.
            max_output_bytes: Per-stream retain limit while draining excess
                (1..16 MiB, default 1 MiB).
            client: ``"local"`` (default) or ``"ssh"``. SSH parameters are
                ignored for local.
            ssh_host: Hostname, IP, or ``Host`` alias from ``~/.ssh/config``.
                Required when ``client="ssh"``.
            ssh_port: Positive SSH port when ``client="ssh"``. No implicit 22.
            ssh_user: SSH username when ``client="ssh"``.
            ssh_password: Optional explicit password (prefer keys/agent).
            ssh_key: Optional private-key path on the host filesystem.
            ssh_flags: Space-separated supported OpenSSH flags: ``-X``,
                ``-Y``, ``-A``, ``-a``, ``-C``. Others are ignored.
            ssh_accept_unknown_host_key: Insecure opt-in to trust a host key
                missing from ``known_hosts``.

        Returns:
            Bounded report with exit code, resolved ``cwd``, duration,
            command, stdout, and stderr when present. Non-zero exits are
            returned, not raised. Formatted text is capped (~100k chars) with
            head and tail preserved.

        Raises:
            ValueError: Empty ``cwd``, invalid client, or missing SSH settings.
            BashError: Invalid command/timeout/interpreter/flags/env/limit, or
                process/SSH start failure.

        Warning:
            Arbitrary code execution with the selected client's permissions.
            Can modify or destroy data.
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
            client=client,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key=ssh_key,
            ssh_flags=ssh_flags,
            ssh_accept_unknown_host_key=ssh_accept_unknown_host_key,
        )
