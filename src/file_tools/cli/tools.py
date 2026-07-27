"""CLI adapters for the four command-line tools.

The CLI mirrors the MCP scalar parameters while keeping command-line concerns
such as stdin handling and process exit codes outside the core tool modules.
"""

from __future__ import annotations

from typing import Mapping
from dataclasses import dataclass

from ..client import get_client as _get_client
from ..tools.apply_patch import apply_patch as _apply_patch
from ..tools.bash import bash as _bash
from ..tools.read import read as _read
from ..tools.write import write as _write


@dataclass(frozen=True)
class CliResult:
    """Text emitted to stdout and the CLI process exit code."""

    stdout: str
    exit_code: int = 0


def _require_cwd(cwd: str) -> str:
    value = (cwd or "").strip()
    if not value:
        raise ValueError("cwd is required")
    return value


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
        client=client or "local",
        cwd=cwd,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key=ssh_key,
        ssh_flags=ssh_flags,
        accept_unknown_host_key=ssh_accept_unknown_host_key,
    )


def read(
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
) -> CliResult:
    """Run the read tool and return its exact model-facing content."""
    cwd = _require_cwd(cwd)
    live_client = _client(
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
    result = _read(
        target_file,
        offset=offset,
        limit=limit,
        show_line_numbers=show_line_numbers,
        client=live_client,
    )
    return CliResult(stdout=result.content)


def write(
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
) -> CliResult:
    """Run the write tool with ``content`` supplied by CLI stdin."""
    cwd = _require_cwd(cwd)
    live_client = _client(
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
    result = _write(file_path, content, client=live_client)
    return CliResult(
        stdout=f"wrote {result.bytes_written} bytes to {result.file_path}\n"
    )


def apply_patch(
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
) -> CliResult:
    """Run apply_patch with the complete patch document supplied by stdin."""
    cwd = _require_cwd(cwd)
    live_client = _client(
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
    result = _apply_patch(patch_text, client=live_client)
    return CliResult(
        stdout=(
            f"added={result.added} "
            f"modified={result.modified} "
            f"deleted={result.deleted}\n"
        )
    )


def bash(
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
) -> CliResult:
    """Run bash and propagate its exit status as the CLI process status."""
    cwd = _require_cwd(cwd)
    live_client = _client(
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
    result = _bash(
        command,
        cwd=cwd,
        timeout=timeout,
        description=description,
        interpreter=interpreter,
        flags=flags,
        env=env,
        stdin=stdin,
        max_output_bytes=max_output_bytes,
        client=live_client,
    )
    exit_code = result.exit_code if 0 <= result.exit_code <= 255 else 1
    return CliResult(stdout=result.format(), exit_code=exit_code)


__all__ = ["apply_patch", "bash", "read", "write"]
