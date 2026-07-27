"""Local and SSH-backed file tools."""

from .client import (
    AuthenticationError,
    Client,
    ClientError,
    CommandResult,
    ConflictError,
    FileInfo,
    FileNotFoundError,
    LocalClient,
    OperationTimeoutError,
    PermissionDeniedError,
    SshClient,
    TransferLimitError,
    clear_client_cache,
    get_cached_client,
    get_client,
    resolve_client,
)
from .tools import apply_patch, bash, edit, read, write

__all__ = [
    "AuthenticationError",
    "Client",
    "ClientError",
    "CommandResult",
    "ConflictError",
    "FileInfo",
    "FileNotFoundError",
    "LocalClient",
    "OperationTimeoutError",
    "PermissionDeniedError",
    "SshClient",
    "TransferLimitError",
    "clear_client_cache",
    "get_cached_client",
    "get_client",
    "resolve_client",
    "read",
    "write",
    "edit",
    "apply_patch",
    "bash",
]


__version__ = "0.1.0"
