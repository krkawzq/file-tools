"""Pluggable terminal clients (local / ssh)."""

from .base import Client, ClientError, CommandResult, normalize_flags
from .factory import clear_client_cache, get_client, resolve_client
from .local import LocalClient
from .password import (
    IncorrectPasswordError,
    PasswordError,
    PasswordMaterial,
    PasswordRequest,
    PasswordSource,
    resolve_password,
)
from .ssh import SshClient, flags_to_paramiko_options, parse_ssh_flags

__all__ = [
    "Client",
    "ClientError",
    "CommandResult",
    "LocalClient",
    "SshClient",
    "get_client",
    "resolve_client",
    "clear_client_cache",
    "normalize_flags",
    "parse_ssh_flags",
    "flags_to_paramiko_options",
    "resolve_password",
    "IncorrectPasswordError",
    "PasswordError",
    "PasswordMaterial",
    "PasswordRequest",
    "PasswordSource",
]
