"""Asynchronous clients for local and SSH-backed file operations."""

from __future__ import annotations

import math
import os
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from threading import Lock
from typing import Any, Literal, TypeVar

from anyio import CapacityLimiter, to_thread

from . import _core

ClientError = _core.ClientError
FileNotFoundError = _core.FileNotFoundError
PermissionDeniedError = _core.PermissionDeniedError
ConflictError = _core.ConflictError
OperationTimeoutError = _core.OperationTimeoutError
AuthenticationError = _core.AuthenticationError
TransferLimitError = _core.TransferLimitError
FileInfo = _core.FileInfo
CommandResult = _core.CommandResult
ClientKind = Literal["local", "ssh"]

_T = TypeVar("_T")
_NativeClient = _core.LocalClient | _core.SshClient


async def _run_blocking(
    function: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Run blocking native work without occupying the event-loop thread."""
    call = partial(function, *args, **kwargs)
    return await to_thread.run_sync(call)


class _AsyncClient:
    kind: ClientKind
    cwd: str

    def __init__(
        self,
        factory: Callable[[], _NativeClient],
        *,
        max_concurrency: int,
    ) -> None:
        if isinstance(max_concurrency, bool) or max_concurrency <= 0:
            raise ValueError("max_concurrency must be a positive integer")
        self._factory = factory
        self._native: _NativeClient | None = None
        self._native_lock = Lock()
        self._limiter = CapacityLimiter(max_concurrency)

    def _get_native(self) -> _NativeClient:
        native = self._native
        if native is not None:
            return native
        with self._native_lock:
            if self._native is None:
                self._native = self._factory()
            return self._native

    async def _call(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        def invoke() -> Any:
            function = getattr(self._get_native(), method)
            return function(*args, **kwargs)

        async with self._limiter:
            return await _run_blocking(invoke)

    async def resolve(self, path: str) -> str:
        return await self._call("resolve", path)

    async def exists(self, path: str) -> bool:
        return await self._call("exists", path)

    async def is_file(self, path: str) -> bool:
        return await self._call("is_file", path)

    async def is_dir(self, path: str) -> bool:
        return await self._call("is_dir", path)

    async def path_info(self, path: str) -> tuple[bool, bool, bool]:
        return await self._call("path_info", path)

    async def stat(self, path: str) -> FileInfo:
        return await self._call("stat", path)

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return await self._call("read_text", path, encoding=encoding)

    async def read_text_window(
        self,
        path: str,
        offset: int,
        limit: int,
        *,
        encoding: str = "utf-8",
    ) -> tuple[str, int, int, int, bool]:
        return await self._call(
            "read_text_window",
            path,
            offset,
            limit,
            encoding=encoding,
        )

    async def read_bytes(self, path: str) -> bytes:
        return await self._call("read_bytes", path)

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> None:
        await self._call("write_text", path, content, encoding=encoding)

    async def write_bytes(self, path: str, data: bytes) -> None:
        await self._call("write_bytes", path, data)

    async def write_text_atomic(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        expected_version: str | None = None,
        create_only: bool = False,
    ) -> FileInfo:
        return await self._call(
            "write_text_atomic",
            path,
            content,
            encoding=encoding,
            expected_version=expected_version,
            create_only=create_only,
        )

    async def mkdir(
        self,
        path: str,
        *,
        parents: bool = True,
        exist_ok: bool = True,
    ) -> None:
        await self._call("mkdir", path, parents=parents, exist_ok=exist_ok)

    async def delete(self, path: str) -> None:
        await self._call("delete", path)

    async def delete_if_version(
        self,
        path: str,
        *,
        expected_version: str | None = None,
    ) -> None:
        await self._call(
            "delete_if_version",
            path,
            expected_version=expected_version,
        )

    async def join(self, *parts: str) -> str:
        return await self._call("join", *parts)

    async def exec_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
        interpreter: str | None = None,
        flags: str | Sequence[str] | None = None,
        max_output_bytes: int | None = None,
    ) -> CommandResult:
        return await self._call(
            "exec_command",
            command,
            cwd=cwd,
            timeout=timeout,
            env=env,
            stdin=stdin,
            interpreter=interpreter,
            flags=flags,
            max_output_bytes=max_output_bytes,
        )


class LocalClient(_AsyncClient):
    """Asynchronous local filesystem client."""

    kind: Literal["local"] = "local"

    def __init__(
        self,
        cwd: str | os.PathLike[str] | None = None,
        *,
        max_transfer_bytes: int = 16 * 1024 * 1024,
        max_concurrency: int = 16,
    ) -> None:
        configured_cwd = os.fspath(cwd) if cwd is not None else os.curdir
        self.cwd = os.path.abspath(os.path.expanduser(configured_cwd))
        super().__init__(
            lambda: _core.LocalClient(
                cwd=configured_cwd,
                max_transfer_bytes=max_transfer_bytes,
            ),
            max_concurrency=max_concurrency,
        )

    def __repr__(self) -> str:
        return f"LocalClient(cwd={self.cwd!r})"


class SshClient(_AsyncClient):
    """Asynchronous OpenSSH client."""

    kind: Literal["ssh"] = "ssh"

    def __init__(
        self,
        host: str,
        *,
        port: int,
        username: str,
        password: str | None = None,
        key_filename: str | None = None,
        cwd: str = ".",
        connect_timeout: float = 30.0,
        operation_timeout: float = 30.0,
        max_transfer_bytes: int = 16 * 1024 * 1024,
        max_concurrency: int = 4,
        multiplexing: bool = True,
        ssh_flags: str | Sequence[str] | None = None,
        allow_password_prompt: bool = True,
        accept_unknown_host_key: bool = False,
    ) -> None:
        host = host.strip()
        username = username.strip()
        if not host:
            raise ValueError("ssh host is required")
        if not username:
            raise ValueError("ssh user is required")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535:
            raise ValueError("ssh port is required and must be a positive integer")
        if not math.isfinite(connect_timeout) or connect_timeout <= 0:
            raise ValueError("connect_timeout must be a positive finite number")
        if not math.isfinite(operation_timeout) or operation_timeout <= 0:
            raise ValueError("operation_timeout must be a positive finite number")
        if host.startswith("-"):
            raise ValueError("ssh host must not start with '-'")

        self.host = host
        self.port = port
        self.username = username
        self.cwd = cwd
        super().__init__(
            lambda: _core.SshClient(
                host,
                port=port,
                username=username,
                password=password,
                key_filename=key_filename,
                cwd=cwd,
                connect_timeout=connect_timeout,
                operation_timeout=operation_timeout,
                max_transfer_bytes=max_transfer_bytes,
                multiplexing=multiplexing,
                ssh_flags=ssh_flags,
                allow_password_prompt=allow_password_prompt,
                accept_unknown_host_key=accept_unknown_host_key,
            ),
            max_concurrency=max_concurrency,
        )

    def __repr__(self) -> str:
        return (
            f"SshClient(host={self.host!r}, port={self.port}, "
            f"username={self.username!r}, cwd={self.cwd!r})"
        )


Client = LocalClient | SshClient


def _require_ssh_parameters(
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
    connect_timeout: float = 30.0,
    operation_timeout: float = 30.0,
    max_transfer_bytes: int = 16 * 1024 * 1024,
    max_concurrency: int | None = None,
    multiplexing: bool = True,
    allow_password_prompt: bool = True,
    accept_unknown_host_key: bool = False,
) -> Client:
    """Create an asynchronous local or SSH client."""
    kind = str(client).strip().lower()

    if kind == "local":
        return LocalClient(
            cwd=cwd or None,
            max_transfer_bytes=max_transfer_bytes,
            max_concurrency=max_concurrency or 16,
        )
    if kind != "ssh":
        raise ValueError(f"unknown client: {kind!r} (expected 'local' or 'ssh')")

    host, port, user = _require_ssh_parameters(ssh_host, ssh_port, ssh_user)
    return SshClient(
        host,
        port=port,
        username=user,
        password=ssh_password or None,
        key_filename=ssh_key or None,
        cwd=cwd or ".",
        connect_timeout=connect_timeout,
        operation_timeout=operation_timeout,
        max_transfer_bytes=max_transfer_bytes,
        max_concurrency=max_concurrency or 4,
        multiplexing=multiplexing,
        ssh_flags=ssh_flags or None,
        allow_password_prompt=allow_password_prompt,
        accept_unknown_host_key=accept_unknown_host_key,
    )


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
    connect_timeout: float = 30.0,
    operation_timeout: float = 30.0,
    max_transfer_bytes: int = 16 * 1024 * 1024,
    max_concurrency: int | None = None,
    multiplexing: bool = True,
    allow_password_prompt: bool = True,
    accept_unknown_host_key: bool = False,
) -> Client:
    """Return an existing client or create an asynchronous client."""
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
        connect_timeout=connect_timeout,
        operation_timeout=operation_timeout,
        max_transfer_bytes=max_transfer_bytes,
        max_concurrency=max_concurrency,
        multiplexing=multiplexing,
        allow_password_prompt=allow_password_prompt,
        accept_unknown_host_key=accept_unknown_host_key,
    )


_CACHE_LOCK = Lock()
_CLIENT_CACHE: OrderedDict[tuple[Any, ...], tuple[float, Client]] = OrderedDict()


def _cache_settings() -> tuple[float, int]:
    try:
        ttl = float(os.environ.get("FILE_TOOLS_CLIENT_CACHE_TTL", "300"))
        size = int(os.environ.get("FILE_TOOLS_CLIENT_CACHE_SIZE", "32"))
    except ValueError as exc:
        raise ValueError("invalid file-tools client cache settings") from exc
    if not math.isfinite(ttl) or ttl < 0 or size <= 0:
        raise ValueError("client cache TTL must be non-negative and size must be positive")
    return ttl, size


def clear_client_cache() -> None:
    """Drop cached clients and their persistent SSH control sockets."""
    with _CACHE_LOCK:
        _CLIENT_CACHE.clear()


def get_cached_client(**settings: Any) -> Client:
    """Return an LRU/TTL-cached client for repeated MCP operations."""
    normalized = tuple(
        sorted(
            (key, tuple(value) if isinstance(value, list) else value)
            for key, value in settings.items()
        )
    )
    ttl, capacity = _cache_settings()
    now = time.monotonic()
    with _CACHE_LOCK:
        expired = [
            key
            for key, (created, _) in _CLIENT_CACHE.items()
            if ttl == 0 or now - created >= ttl
        ]
        for key in expired:
            _CLIENT_CACHE.pop(key, None)
        cached = _CLIENT_CACHE.pop(normalized, None)
        if cached is not None:
            _CLIENT_CACHE[normalized] = cached
            return cached[1]
        client = get_client(**settings)
        _CLIENT_CACHE[normalized] = (now, client)
        while len(_CLIENT_CACHE) > capacity:
            _CLIENT_CACHE.popitem(last=False)
        return client


__all__ = [
    "Client",
    "ClientError",
    "FileNotFoundError",
    "PermissionDeniedError",
    "ConflictError",
    "OperationTimeoutError",
    "AuthenticationError",
    "TransferLimitError",
    "FileInfo",
    "ClientKind",
    "CommandResult",
    "LocalClient",
    "SshClient",
    "get_client",
    "get_cached_client",
    "clear_client_cache",
    "resolve_client",
]
