"""Argument parser and console entry point for ``file-tools``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from . import tools as _tools


def _read_text_stream(stream: TextIO) -> str:
    """Read stdin without platform newline translation when a buffer exists."""
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return stream.read()
    encoding = getattr(stream, "encoding", None) or "utf-8"
    return buffer.read().decode(encoding, errors="replace")


def _write_text_stream(stream: TextIO, content: str) -> None:
    """Write protocol output without Windows CRLF double translation."""
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(content)
        stream.flush()
        return
    encoding = getattr(stream, "encoding", None) or "utf-8"
    buffer.write(content.encode(encoding, errors="replace"))
    buffer.flush()


def _parse_env_assignments(assignments: Sequence[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for assignment in assignments or ():
        if "=" not in assignment:
            raise ValueError(
                f"invalid --env value {assignment!r}; expected NAME=VALUE"
            )
        key, value = assignment.split("=", 1)
        if not key:
            raise ValueError("invalid --env value; variable name is empty")
        env[key] = value
    return env


def _add_client_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cwd",
        required=True,
        help="working directory on the selected client (required)",
    )
    parser.add_argument(
        "--client",
        choices=("local", "ssh"),
        default="local",
        help="filesystem/execution backend (default: local)",
    )
    parser.add_argument("--ssh-host", default="", help="SSH hostname or IP")
    parser.add_argument(
        "--ssh-port",
        type=int,
        default=0,
        help="SSH port; required and positive when --client=ssh",
    )
    parser.add_argument("--ssh-user", default="", help="SSH username")
    parser.add_argument(
        "--ssh-password",
        default="",
        help="explicit SSH password; key authentication is preferred",
    )
    parser.add_argument(
        "--ssh-key",
        default="",
        help="private-key path on the machine running file-tools",
    )
    parser.add_argument(
        "--ssh-flags",
        default="",
        help='supported OpenSSH-style flags, for example "-A -C"',
    )
    parser.add_argument(
        "--ssh-accept-unknown-host-key",
        action="store_true",
        help="insecure opt-in: trust an SSH host key absent from known_hosts",
    )


def create_parser() -> argparse.ArgumentParser:
    """Build the top-level parser and its four MCP-aligned subcommands."""
    parser = argparse.ArgumentParser(
        prog="file-tools",
        description=(
            "Local/SSH file tools aligned with the MCP interface. "
            "write and apply_patch read their complete input from stdin."
        ),
    )
    subparsers = parser.add_subparsers(dest="tool", required=True)

    read_parser = subparsers.add_parser(
        "read",
        help="read a UTF-8 text file",
        description="Read a selected line window from a UTF-8 text file.",
    )
    read_parser.add_argument("target_file", help="file to read")
    read_parser.add_argument(
        "--offset",
        type=int,
        default=1,
        help="1-based start line; negative values read the tail (default: 1)",
    )
    read_parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="maximum lines for a non-negative offset (default: 2000)",
    )
    read_parser.add_argument(
        "--show-line-numbers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="show cat -n style line numbers (default: enabled)",
    )
    _add_client_arguments(read_parser)
    read_parser.set_defaults(_handler=_tools.read, _reads_stdin=False)

    write_parser = subparsers.add_parser(
        "write",
        help="create or overwrite a file from stdin",
        description=(
            "Create or completely overwrite a UTF-8 text file. "
            "The complete file content is read from stdin."
        ),
    )
    write_parser.add_argument("file_path", help="destination file")
    _add_client_arguments(write_parser)
    write_parser.set_defaults(_handler=_tools.write, _reads_stdin=True)

    patch_parser = subparsers.add_parser(
        "apply_patch",
        help="apply a Codex patch from stdin",
        description="Read a complete Codex patch from stdin and apply it.",
    )
    _add_client_arguments(patch_parser)
    patch_parser.set_defaults(_handler=_tools.apply_patch, _reads_stdin=True)

    bash_parser = subparsers.add_parser(
        "bash",
        help="execute a foreground shell command",
        description=(
            "Execute a foreground command and propagate its exit status. "
            "No sandbox or command filtering is applied."
        ),
    )
    bash_parser.add_argument(
        "command",
        help="shell command string; quote it as one argument",
    )
    bash_parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="timeout in seconds; 0 disables it (default: 120)",
    )
    bash_parser.add_argument(
        "--description",
        default="",
        help="optional purpose included in formatted output",
    )
    bash_parser.add_argument(
        "--interpreter",
        default="auto",
        help="interpreter executable (default: auto; cmd on Windows, bash elsewhere)",
    )
    bash_parser.add_argument(
        "--flags",
        default="",
        help="additional interpreter flags; the command flag is injected",
    )
    bash_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="child environment override; may be repeated",
    )
    bash_parser.add_argument(
        "--stdin",
        action="store_true",
        dest="read_stdin",
        help="read command stdin from file-tools standard input",
    )
    bash_parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=1024 * 1024,
        help="per-stream retained output limit (default: 1048576)",
    )
    _add_client_arguments(bash_parser)
    bash_parser.set_defaults(_handler=_tools.bash, _reads_stdin=False)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Parse CLI arguments, run one tool, and return its process exit code."""
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr

    namespace = create_parser().parse_args(argv)
    arguments = vars(namespace)
    handler = arguments.pop("_handler")
    reads_stdin = arguments.pop("_reads_stdin")
    bash_reads_stdin = arguments.pop("read_stdin", False)
    arguments.pop("tool")

    if arguments.get("ssh_port") == 0:
        arguments["ssh_port"] = None
    if reads_stdin:
        input_name = "content" if handler is _tools.write else "patch_text"
        arguments[input_name] = _read_text_stream(input_stream)
    elif bash_reads_stdin:
        arguments["stdin"] = _read_text_stream(input_stream)

    try:
        if handler is _tools.bash:
            arguments["env"] = _parse_env_assignments(arguments.get("env"))
        result = handler(**arguments)
    except Exception as exc:
        error_stream.write(f"error: {exc}\n")
        return 1

    _write_text_stream(output_stream, result.stdout)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
