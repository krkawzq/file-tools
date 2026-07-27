"""Create and cache local or SSH clients from scalar configuration."""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from functools import lru_cache
from typing import Literal, Sequence

from .base import Client, normalize_flags
from .local import LocalClient
from .password import IncorrectPasswordError
from .ssh import SshClient

ClientKind = Literal["local", "ssh"]

_DEFAULT_CACHE_SIZE = 32
_SSH_CACHE_LOCK = threading.RLock()
_SSH_CACHE: OrderedDict[tuple[object, ...], Client] = OrderedDict()


@lru_cache(maxsize=_DEFAULT_CACHE_SIZE)
def _cached_local(cwd: str) -> Client:
    return LocalClient(cwd=cwd or None)


def _password_fingerprint(password: str) -> bytes:
    """Return a non-reversible cache discriminator without retaining plaintext."""
    return hashlib.sha256(password.encode("utf-8")).digest() if password else b""


def _get_cached_ssh(
    host: str,
    port: int,
    user: str,
    password: str,
    key: str,
    flags: tuple[str, ...],
    cwd: str,
    allow_password_prompt: bool,
    accept_unknown_host_key: bool,
) -> Client:
    key_tuple: tuple[object, ...] = (
        host,
        port,
        user,
        _password_fingerprint(password),
        key,
        flags,
        cwd,
        allow_password_prompt,
        accept_unknown_host_key,
    )
    with _SSH_CACHE_LOCK:
        cached = _SSH_CACHE.get(key_tuple)
        if cached is not None:
            if not isinstance(cached, SshClient) or cached.is_active:
                _SSH_CACHE.move_to_end(key_tuple)
                return cached
            _SSH_CACHE.pop(key_tuple)
            cached.close()

    created = SshClient(
        host=host,
        port=port,
        username=user,
        password=password or None,
        key_filename=key or None,
        cwd=cwd or ".",
        ssh_flags=flags or None,
        allow_password_prompt=allow_password_prompt,
        accept_unknown_host_key=accept_unknown_host_key,
    )
    with _SSH_CACHE_LOCK:
        raced = _SSH_CACHE.get(key_tuple)
        if raced is not None:
            if not isinstance(raced, SshClient) or raced.is_active:
                created.close()
                _SSH_CACHE.move_to_end(key_tuple)
                return raced
            _SSH_CACHE.pop(key_tuple)
            raced.close()
        _SSH_CACHE[key_tuple] = created
        while len(_SSH_CACHE) > _DEFAULT_CACHE_SIZE:
            _, evicted = _SSH_CACHE.popitem(last=False)
            if isinstance(evicted, SshClient):
                evicted.close()
    return created


def _require_ssh_params(
    *,
    ssh_host: str,
    ssh_port: int | None,
    ssh_user: str,
) -> tuple[str, int, str]:
    host = (ssh_host or "").strip()
    user = (ssh_user or "").strip()
    if not host:
        raise ValueError("ssh_host is required when client='ssh'")
    if ssh_port is None or int(ssh_port) <= 0:
        raise ValueError("ssh_port is required when client='ssh' (positive integer)")
    if not user:
        raise ValueError("ssh_user is required when client='ssh'")
    return host, int(ssh_port), user


def get_client(
    *,
    client: str = "local",
    cwd: str = "",
    ssh_host: str = "",
    ssh_port: int | None = None,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
    ssh_flags: str | Sequence[str] = "",
    allow_password_prompt: bool = True,
    accept_unknown_host_key: bool = False,
    client_type: str | None = None,
) -> Client:
    """Return a cached local or SSH client built from scalar parameters.

    ``client_type`` is accepted as a compatibility spelling and takes
    precedence over ``client`` when supplied. SSH clients require an explicit
    host, positive port, and username. Password fingerprints, key paths,
    supported SSH flags, and host-key policy participate in the cache key, so
    different connection settings never share a client instance. Unknown host
    keys are rejected unless ``accept_unknown_host_key`` is explicitly true.
    """
    kind = (client_type if client_type is not None else client) or "local"
    kind = str(kind).strip().lower() or "local"

    if kind == "local":
        return _cached_local(cwd or "")

    if kind == "ssh":
        host, port, user = _require_ssh_params(
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
        )
        if isinstance(ssh_flags, str):
            flags = tuple(normalize_flags(ssh_flags, posix=True))
        else:
            flags = tuple(str(item) for item in (ssh_flags or ()))
        try:
            return _get_cached_ssh(
                host,
                port,
                user,
                ssh_password or "",
                ssh_key or "",
                flags,
                cwd or "",
                allow_password_prompt,
                accept_unknown_host_key,
            )
        except IncorrectPasswordError as exc:
            raise ValueError(str(exc)) from exc

    raise ValueError(f"unknown client: {kind!r} (expected 'local' or 'ssh')")


def resolve_client(
    live: Client | None = None,
    *,
    client: str = "local",
    cwd: str = "",
    ssh_host: str = "",
    ssh_port: int | None = None,
    ssh_user: str = "",
    ssh_password: str = "",
    ssh_key: str = "",
    ssh_flags: str | Sequence[str] = "",
    allow_password_prompt: bool = True,
    accept_unknown_host_key: bool = False,
    client_type: str | None = None,
) -> Client:
    """Return ``live`` unchanged, or create a client from scalar parameters."""
    if live is not None:
        return live
    return get_client(
        client=client,
        cwd=cwd,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key=ssh_key,
        ssh_flags=ssh_flags,
        allow_password_prompt=allow_password_prompt,
        accept_unknown_host_key=accept_unknown_host_key,
        client_type=client_type,
    )


def clear_client_cache() -> None:
    """Clear cached clients and deterministically close SSH/SFTP resources."""
    _cached_local.cache_clear()
    with _SSH_CACHE_LOCK:
        clients = list(_SSH_CACHE.values())
        _SSH_CACHE.clear()
    for client in clients:
        if isinstance(client, SshClient):
            client.close()


__all__ = [
    "ClientKind",
    "clear_client_cache",
    "get_client",
    "resolve_client",
]
