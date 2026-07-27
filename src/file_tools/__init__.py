"""Local and SSH-backed file tools."""

from .client import (
    Client,
    ClientError,
    CommandResult,
    LocalClient,
    SshClient,
    get_client,
    resolve_client,
)
from .tools import apply_patch, bash, edit, read, write

__all__ = [
    "Client",
    "ClientError",
    "CommandResult",
    "LocalClient",
    "SshClient",
    "get_client",
    "resolve_client",
    "read",
    "write",
    "edit",
    "apply_patch",
    "bash",
]


__version__ = "0.1.0"
