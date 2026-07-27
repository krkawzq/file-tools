"""Shell-command tool built on :meth:`Client.exec_command`.

The tool intentionally applies no command-content filtering, sandboxing,
approval gates, or background-task policy.  The selected shell receives the
command exactly as provided.  ``cwd`` remains explicit on every call so local
and remote executions have the same, predictable interface.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..client import Client, ClientError, CommandResult
from ..client import resolve_client as _resolve_client
from ..client._output import (
    DEFAULT_MAX_OUTPUT_BYTES,
    validate_max_output_bytes,
)
from ..client.base import (
    format_argv,
    inject_cmd_flag,
    normalize_env,
    normalize_flags,
)

DEFAULT_INTERPRETER = "auto"
DEFAULT_FLAGS = ""
DEFAULT_TIMEOUT_SECS = 120.0
MAX_OUTPUT_CHARS = 100_000


class BashError(Exception):
    """Raised when a shell invocation cannot be constructed or started."""


@dataclass
class BashResult:
    """Result of a shell-command invocation."""

    command: str
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    signal: int | None = None
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    stdout_omitted_bytes: int = 0
    stderr_omitted_bytes: int = 0
    description: str = ""
    interpreter: str = DEFAULT_INTERPRETER
    # User-supplied interpreter flags, excluding the injected command flag.
    flags: str = ""
    # Effective interpreter invocation, including the injected command flag.
    invocation: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def __bool__(self) -> bool:
        return self.ok

    @property
    def truncated(self) -> bool:
        return self.stdout_omitted_bytes > 0 or self.stderr_omitted_bytes > 0

    def format(self, *, max_chars: int = MAX_OUTPUT_CHARS) -> str:
        """Return a compact, bounded, model-facing representation."""
        header_bits = [
            f"exit: {self.exit_code}",
            f"cwd: {self.cwd}",
            f"duration_ms: {self.duration_ms}",
        ]
        if self.timed_out:
            header_bits.insert(1, "timed_out")
        if self.signal is not None:
            header_bits.append(f"signal: {self.signal}")
        omitted_bytes = self.stdout_omitted_bytes + self.stderr_omitted_bytes
        if omitted_bytes:
            header_bits.append(f"output_truncated: {omitted_bytes} bytes omitted")
        if self.description:
            header_bits.append(f"desc: {self.description}")

        lines = [" | ".join(header_bits), f"$ {self.command}"]
        if self.stdout:
            lines.append(self.stdout.rstrip("\n"))
        if self.stderr:
            lines.extend(("[stderr]", self.stderr.rstrip("\n")))

        text = "\n".join(lines)
        if not text.endswith("\n"):
            text += "\n"
        if max_chars <= 0 or len(text) <= max_chars:
            return text

        marker = "\n... [truncated] ...\n"
        if max_chars <= len(marker):
            return text[:max_chars]

        for _ in range(2):
            keep = max_chars - len(marker)
            head_chars = (keep + 1) // 2
            tail_chars = keep // 2
            omitted = len(text) - head_chars - tail_chars
            marker = f"\n... [truncated, {omitted} chars omitted] ...\n"

        keep = max_chars - len(marker)
        if keep <= 0:
            return text[:max_chars]
        head_chars = (keep + 1) // 2
        tail_chars = keep // 2
        return text[:head_chars] + marker + (
            text[-tail_chars:] if tail_chars else ""
        )

    def __str__(self) -> str:
        return self.format()


def bash(
    command: str,
    *,
    cwd: str | os.PathLike[str],
    timeout: float | None = DEFAULT_TIMEOUT_SECS,
    description: str = "",
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
    interpreter: str = DEFAULT_INTERPRETER,
    flags: str | Sequence[str] | None = DEFAULT_FLAGS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    client: Client | None = None,
) -> BashResult:
    """Execute a foreground command through a selected interpreter.

    By default, ``interpreter="auto"`` selects ``cmd`` for a local Windows
    client and ``bash`` for local Linux/macOS or an SSH client. The supplied
    string runs in the explicit ``cwd`` and is passed through without content
    filtering, so syntax is interpreted by that selected shell.

    Known interpreters automatically receive their command-string flag:
    ``-c`` for POSIX shells, Python, Ruby, and Perl; ``-Command`` for
    PowerShell; and ``/c`` for ``cmd``. Pass only additional interpreter
    options through ``flags``. Non-zero exits and timeouts are returned as
    :class:`BashResult` values rather than raised as exceptions.

    Args:
        command: Non-empty command or multiline script string.
        cwd: Required command working directory.
        timeout: Maximum seconds to wait. ``0`` or ``None`` disables the
            timeout; negative and non-finite values are rejected.
        description: Optional purpose included in formatted output.
        env: Environment variables overlaid on the child process.
        stdin: Optional text sent to standard input.
        interpreter: Executable name/path, or ``"auto"`` for a platform
            default. Auto selects ``cmd`` on local Windows and ``bash``
            elsewhere.
        flags: Additional interpreter flags, parsed with POSIX rules for
            Linux/macOS/SSH and Windows rules for local Windows. Usually omit
            the command-string flag because it is injected.
        max_output_bytes: Per-stream retained-output limit in bytes. Defaults
            to 1 MiB and may not exceed 16 MiB. Excess output is still drained
            to avoid blocking but only its beginning and end are retained.
        client: Existing local or SSH client. When omitted, use the cached
            local client rooted at the process working directory.

    Returns:
        A :class:`BashResult` containing exit status, captured stdout/stderr,
        resolved cwd, duration, timeout state, and invocation metadata.
        Command capture is bounded while the process runs; total and omitted
        byte counts remain available on the result. ``BashResult.format()``
        applies a second model-facing cap of 100,000 characters while retaining
        both its beginning and end.

    Raises:
        BashError: If arguments are invalid or command execution cannot start.

    Warning:
        This function provides no sandbox, approval gate, command filtering, or
        background-task manager. It can execute arbitrary programs and modify
        data with the selected client's permissions.
    """
    if not isinstance(command, str) or not command.strip():
        raise BashError("command must be a non-empty string")

    if cwd is None:
        raise BashError("cwd is required and must be a non-empty path")
    try:
        workdir = os.fspath(cwd)
    except TypeError as exc:
        raise BashError("cwd must be a string or path-like object") from exc
    if not isinstance(workdir, str):
        raise BashError("cwd must resolve to a text path")
    workdir = workdir.strip()
    if not workdir:
        raise BashError("cwd is required and must be a non-empty path")

    if timeout is None:
        effective_timeout = None
    else:
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as exc:
            raise BashError(
                "timeout must be a non-negative finite number or None"
            ) from exc
        if not math.isfinite(timeout_value) or timeout_value < 0:
            raise BashError("timeout must be a non-negative finite number or None")
        effective_timeout = None if timeout_value == 0 else timeout_value

    if not isinstance(interpreter, str):
        raise BashError("interpreter must be a string")
    requested_interpreter = interpreter.strip() or DEFAULT_INTERPRETER
    client_kind = client.kind if client is not None else "local"
    posix_target = client_kind == "ssh" or os.name != "nt"
    if requested_interpreter.casefold() == DEFAULT_INTERPRETER:
        interp = "bash" if posix_target else "cmd"
    else:
        interp = requested_interpreter
    try:
        flag_list = normalize_flags(flags, posix=posix_target)
    except (TypeError, ValueError) as exc:
        raise BashError(f"invalid interpreter flags: {exc}") from exc
    try:
        output_limit = validate_max_output_bytes(max_output_bytes)
    except ValueError as exc:
        raise BashError(str(exc)) from exc
    try:
        normalized_env = normalize_env(env)
    except ValueError as exc:
        raise BashError(str(exc)) from exc
    if stdin is not None:
        if not isinstance(stdin, str):
            raise BashError("stdin must be valid UTF-8 text")
        try:
            stdin.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BashError("stdin must be valid UTF-8 text") from exc

    effective_flags = inject_cmd_flag(interp, flag_list)
    flags_display = format_argv(flag_list, posix=posix_target)
    invocation = format_argv([interp, *effective_flags], posix=posix_target)
    c = _resolve_client(client)

    try:
        result: CommandResult = c.exec_command(
            command,
            cwd=workdir,
            timeout=effective_timeout,
            env=normalized_env,
            stdin=stdin,
            interpreter=interp,
            flags=flag_list,
            max_output_bytes=output_limit,
        )
    except ClientError as exc:
        raise BashError(str(exc)) from exc

    return BashResult(
        command=command,
        cwd=result.cwd or workdir,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        timed_out=result.timed_out,
        signal=result.signal,
        stdout_total_bytes=result.stdout_total_bytes,
        stderr_total_bytes=result.stderr_total_bytes,
        stdout_omitted_bytes=result.stdout_omitted_bytes,
        stderr_omitted_bytes=result.stderr_omitted_bytes,
        description=str(description or ""),
        interpreter=interp,
        flags=flags_display,
        invocation=invocation,
    )


__all__ = ["bash"]
