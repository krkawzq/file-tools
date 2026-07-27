"""Client protocol — a terminal that can touch files and run commands."""

from __future__ import annotations

import math
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable

from ._output import DEFAULT_MAX_OUTPUT_BYTES


class ClientError(Exception):
    """Client I/O or execution error."""


def normalize_timeout(timeout: float | None) -> float | None:
    """Validate a public client timeout; zero and ``None`` disable it."""
    if timeout is None:
        return None
    if isinstance(timeout, bool):
        raise ValueError("timeout must be a non-negative finite number or None")
    try:
        value = float(timeout)
    except (TypeError, ValueError) as e:
        raise ValueError(
            "timeout must be a non-negative finite number or None"
        ) from e
    if not math.isfinite(value) or value < 0:
        raise ValueError("timeout must be a non-negative finite number or None")
    return None if value == 0 else value


def _split_windows_flags(value: str) -> list[str]:
    """Split a Windows-style option string without consuming backslashes.

    ``shlex.split`` in its default POSIX mode turns an unquoted path such as
    ``C:\\Temp`` into ``C:Temp``.  Non-POSIX shlex mode preserves Windows path
    separators; remove only matching quote wrappers from its tokens.
    """
    tokens = shlex.split(value, posix=False)
    return [
        token[1:-1]
        if len(token) >= 2
        and token[0] == token[-1]
        and token[0] in {"'", '"'}
        else token
        for token in tokens
    ]


def normalize_flags(
    flags: str | Sequence[str] | None,
    *,
    posix: bool | None = None,
) -> list[str]:
    """Normalize ``flags`` to an argv list.

    - ``None`` / empty → ``[]``
    - ``str`` → target-platform parsing (e.g. ``"-lc"`` → ``["-lc"]``,
      ``"-l -c"`` → ``["-l", "-c"]``); Windows parsing preserves ``\\``
    - sequence → ``[str(x) for x in flags]``
    """
    if flags is None:
        return []
    if isinstance(flags, str):
        flags = flags.strip()
        if not flags:
            return []
        use_posix = os.name != "nt" if posix is None else posix
        return shlex.split(flags) if use_posix else _split_windows_flags(flags)
    return [str(x) for x in flags]


_PORTABLE_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_env(env: Mapping[str, str] | None) -> dict[str, str]:
    """Validate portable child environment overrides.

    The SSH backend has to render names through a POSIX shell, while local
    execution may use Windows.  Restrict names to the intersection accepted by
    both instead of letting a value work locally and fail remotely.
    """
    if env is None:
        return {}
    if not isinstance(env, Mapping):
        raise ValueError("env must be a mapping of variable names to values")

    normalized: dict[str, str] = {}
    for raw_key, raw_value in env.items():
        key = str(raw_key)
        value = str(raw_value)
        if not _PORTABLE_ENV_NAME.fullmatch(key):
            raise ValueError(f"invalid environment variable name: {key!r}")
        if "\x00" in value:
            raise ValueError(f"environment variable {key!r} contains a NUL byte")
        normalized[key] = value
    return normalized


# ── Interpreter → command-string flag mapping ──────────────────────────
# Client 层负责为已知的解释器追加命令字符串 flag（-c / -Command / /c），
# 工具层不需要关心执行细节。
_INTERPRETER_CMD_FLAGS: dict[str, str] = {
    "bash": "-c",
    "sh": "-c",
    "zsh": "-c",
    "dash": "-c",
    "ksh": "-c",
    "fish": "-c",
    "python": "-c",
    "python3": "-c",
    "ruby": "-c",
    "perl": "-c",
    "pwsh": "-Command",
    "powershell": "-Command",
    "cmd": "/c",
}


def inject_cmd_flag(interpreter: str, flags: list[str]) -> list[str]:
    """Append the command-string flag (``-c``, ``-Command``, ``/c``) when
    the interpreter uses one and ``flags`` do not already carry it.

    This is the **single place** where execution semantics (how to tell an
    interpreter to run a script string) are encoded.  Tool layers above
    only deal with tool-level flags such as ``-i`` / ``-l`` and never
    think about ``-c``.

    Args:
        interpreter: Executable basename (e.g. ``"bash"``, ``"python3"``).
        flags: Normalized flag list (e.g. ``["-i", "-l"]``).

    Returns:
        A new list with the command-string flag appended when needed,
        or ``flags`` unchanged when:

        * The interpreter is not in the known map.
        * ``flags`` already contain ``-c`` / ``-Command`` / ``/c``
          (standalone or as a combined short option like ``-ilc``).
    """
    name = os.path.basename(interpreter).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    expected = _INTERPRETER_CMD_FLAGS.get(name)
    if expected is None and re.fullmatch(r"python(?:3(?:\.\d+)?)?", name):
        expected = "-c"
    if expected is None:
        return flags  # unknown interpreter, leave as-is

    for f in flags:
        if f.casefold() == expected.casefold():
            return flags  # already present
        # Combined short flag: -ilc → contains c → has -c built-in.
        if (
            expected == "-c"
            and f.startswith("-")
            and not f.startswith("--")
            and "c" in f[1:].casefold()
        ):
            return flags

    return [*flags, expected]


def format_argv(argv: Sequence[str], *, posix: bool | None = None) -> str:
    """Format argv for diagnostics using the target platform's quoting."""
    use_posix = os.name != "nt" if posix is None else posix
    values = [str(value) for value in argv]
    return shlex.join(values) if use_posix else subprocess.list2cmdline(values)


@dataclass(frozen=True)
class CommandResult:
    """Result of :meth:`Client.exec_command`.

    Designed for a future Bash tool: capture exit status, streams, cwd used,
    wall time, and whether the process hit the timeout.
    """

    exit_code: int
    stdout: str
    stderr: str
    command: str = ""
    cwd: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    signal: int | None = None
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    stdout_omitted_bytes: int = 0
    stderr_omitted_bytes: int = 0
    # Extra metadata (interpreter, flags, …); kept open for Bash tool needs.
    extras: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def __bool__(self) -> bool:
        return self.ok

    @property
    def truncated(self) -> bool:
        return self.stdout_omitted_bytes > 0 or self.stderr_omitted_bytes > 0



@runtime_checkable
class Client(Protocol):
    """Executable task terminal used by tools.

    Paths are opaque strings for the client (local or remote POSIX paths).
    Tools call :meth:`resolve` before file I/O.
    """

    @property
    def kind(self) -> str:
        """``"local"`` or ``"ssh"``."""
        ...

    @property
    def cwd(self) -> str:
        """Working directory used for relative path resolution and commands."""
        ...

    def resolve(self, path: str) -> str:
        """Resolve ``path`` against cwd (and expand ``~`` when applicable)."""
        ...

    def exists(self, path: str) -> bool: ...

    def is_file(self, path: str) -> bool: ...

    def is_dir(self, path: str) -> bool: ...

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str: ...

    def read_bytes(self, path: str) -> bytes: ...

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None: ...

    def write_bytes(self, path: str, data: bytes) -> None: ...

    def mkdir(self, path: str, *, parents: bool = True, exist_ok: bool = True) -> None: ...

    def delete(self, path: str) -> None:
        """Delete a file (not a directory)."""
        ...

    def join(self, *parts: str) -> str:
        """Join path components using the client's separator rules."""
        ...

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
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandResult:
        """Execute a command on this client.

        This is the primary execution API for the bash tool.

        Args:
            command: Command script / argument string. Meaning depends on
                ``interpreter``:

                - ``interpreter is None``: run with the platform default shell
                  (``shell=True`` locally; remote login shell via ssh).
                - ``interpreter`` set (e.g. ``"bash"``, ``"/bin/bash"``):
                  the client **automatically appends** the command-string flag
                  (``-c``, ``-Command``, ``/c``) after *flags* via
                  :func:`inject_cmd_flag`.  Tool callers should pass
                  tool-level flags only (e.g. ``"-il"``) and never ``-c``.

            cwd: Working directory for the command. Relative paths resolve
                against the client's default :attr:`cwd`. ``None`` uses the
                client default.
            timeout: Max seconds to wait. On timeout implementations should
                return a result with ``timed_out=True`` (and best-effort
                partial stdout/stderr) rather than hang indefinitely.
            env: Extra environment variables merged into the process env
                (local) or exported before the command (ssh).
            stdin: Optional text written to the process standard input.
            interpreter: Executable used to run ``command``. ``None`` means
                the default shell. Examples: ``"bash"``, ``"sh"``,
                ``"/usr/bin/bash"``, ``"python3"``.
            flags: Tool-level interpreter flags (e.g. ``"-il"`` for an
                interactive login shell).  The command-string flag (``-c`` /
                ``-Command`` / ``/c``) is **automatically appended** by the
                client — do not include it here.  Accepts a string
                (target-platform parsed, e.g. ``"-l -i"``) or a sequence.
            max_output_bytes: Maximum bytes retained independently for stdout
                and stderr. Clients must continue draining excess bytes while
                preserving only a bounded head and tail.

        Returns:
            :class:`CommandResult` with exit code and captured streams.
            ``extras`` may include ``interpreter`` / ``flags`` when set.
        """
        ...


    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> CommandResult:
        """Alias for :meth:`exec_command` (backward compatible)."""
        ...
