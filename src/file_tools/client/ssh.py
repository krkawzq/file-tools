"""SSH terminal client (paramiko SFTP + exec).

Requires optional dependency: ``pip install 'file-tools[ssh]'`` / paramiko.

Connection parameters ``host`` / ``port`` / ``username`` are always explicit.
Password acquisition supports an explicit value or an interactive TTY prompt
and feeds Paramiko in-process — it never shells out to OpenSSH or ``sshpass``.
"""

from __future__ import annotations

import posixpath
import re
import secrets
import shlex
import stat
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._output import (
    DEFAULT_MAX_OUTPUT_BYTES,
    OUTPUT_READ_CHUNK_BYTES,
    TERMINATION_GRACE_SECS,
    HeadTailBytes,
    validate_max_output_bytes,
)
from .base import (
    ClientError,
    CommandResult,
    inject_cmd_flag,
    normalize_env,
    normalize_flags,
    normalize_timeout,
)
from .password import (
    IncorrectPasswordError,
    PasswordError,
    PasswordRequest,
)


def parse_ssh_flags(flags: str | Sequence[str] | None) -> tuple[str, ...]:
    """Normalize OpenSSH-style flags into an immutable argument sequence."""
    if flags is None or flags == "":
        return ()
    if isinstance(flags, str):
        value = flags.strip()
        return tuple(shlex.split(value)) if value else ()
    return tuple(str(item) for item in flags)


def flags_to_paramiko_options(flags: Sequence[str]) -> dict[str, Any]:
    """Translate supported OpenSSH flags to Paramiko connection options.

    Supported flags are ``-X``/``-Y`` for X11 forwarding, ``-A``/``-a`` for
    enabling/disabling agent use, and ``-C`` for compression. Unknown flags are
    ignored because Paramiko has no general OpenSSH argv passthrough.
    """
    options: dict[str, Any] = {
        "allow_agent": None,
        "compress": False,
        "enable_x11": False,
        "x11_trusted": False,
    }
    for flag in flags:
        if flag == "-X":
            options["enable_x11"] = True
        elif flag == "-Y":
            options["enable_x11"] = True
            options["x11_trusted"] = True
        elif flag == "-A":
            options["allow_agent"] = True
        elif flag == "-a":
            options["allow_agent"] = False
        elif flag == "-C":
            options["compress"] = True
    return options


class SshClient:
    """Remote files via SFTP + remote shell via SSH exec."""

    kind = "ssh"

    def __init__(
        self,
        host: str,
        *,
        port: int,
        username: str,
        password: str | None = None,
        key_filename: str | None = None,
        look_for_keys: bool = True,
        cwd: str = ".",
        ssh_client: Any | None = None,
        connect_timeout: float = 30.0,
        ssh_flags: str | Sequence[str] | None = None,
        allow_password_prompt: bool = True,
        accept_unknown_host_key: bool = False,
    ) -> None:
        try:
            import paramiko
        except ImportError as e:
            raise ImportError(
                "SshClient requires paramiko. Install with: "
                "uv add --optional ssh paramiko  or  pip install 'file-tools[ssh]'"
            ) from e

        host = (host or "").strip()
        username = (username or "").strip()
        if not host:
            raise ValueError("ssh host is required")
        if not username:
            raise ValueError("ssh user is required")
        if port is None or int(port) <= 0:
            raise ValueError("ssh port is required and must be a positive integer")
        port = int(port)

        self._paramiko = paramiko
        self._owns_client = ssh_client is None
        self._host = host
        self._port = port
        self._username = username
        self._enable_x11 = False
        self._x11_trusted = False

        flag_list = (
            normalize_flags(ssh_flags, posix=True)
            if ssh_flags is not None
            else []
        )
        flag_opts = flags_to_paramiko_options(flag_list)
        self._enable_x11 = bool(flag_opts.get("enable_x11"))
        self._x11_trusted = bool(flag_opts.get("x11_trusted"))
        if flag_opts.get("allow_agent") is not None:
            allow_agent = bool(flag_opts["allow_agent"])
        else:
            allow_agent = look_for_keys
        compress = bool(flag_opts.get("compress"))

        key_path = None
        if key_filename:
            key_path = str(Path(key_filename).expanduser())

        if ssh_client is not None:
            self._client = ssh_client
        else:
            self._client = paramiko.SSHClient()
            self._client.load_system_host_keys()
            if accept_unknown_host_key:
                self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            else:
                self._client.set_missing_host_key_policy(paramiko.RejectPolicy())
            self._authenticate(
                paramiko=paramiko,
                host=host,
                port=port,
                username=username,
                password=password or "",
                key_path=key_path,
                look_for_keys=look_for_keys,
                allow_agent=allow_agent,
                compress=compress,
                connect_timeout=connect_timeout,
                allow_password_prompt=allow_password_prompt,
            )

        try:
            self._sftp = self._client.open_sftp()
        except Exception as e:
            if self._owns_client:
                self._client.close()
            raise ClientError(f"SFTP open failed: {e}") from e

        try:
            self._home = self._sftp.normalize(".")
        except OSError:
            self._home = "."

        if cwd in (".", ""):
            self._cwd = self._home
        elif cwd == "~":
            self._cwd = self._home
        elif cwd.startswith("~/"):
            self._cwd = posixpath.normpath(posixpath.join(self._home, cwd[2:]))
        elif cwd.startswith("/"):
            self._cwd = posixpath.normpath(cwd)
        else:
            self._cwd = posixpath.normpath(posixpath.join(self._home, cwd))

    def _authenticate(
        self,
        *,
        paramiko: Any,
        host: str,
        port: int,
        username: str,
        password: str,
        key_path: str | None,
        look_for_keys: bool,
        allow_agent: bool,
        compress: bool,
        connect_timeout: float,
        allow_password_prompt: bool,
    ) -> None:
        """Connect with explicit password (optional) + key/agent.

        Flow:
        1. Resolve password (explicit, else optional TTY prompt on retry).
        2. Attempt connect (key/agent ± password).
        3. On ``AuthenticationException``:
           - if password was already used → incorrect password
           - else prompt once (if allowed) and retry once
           - else fail with a clear message
        """
        req = PasswordRequest(
            password=password,
            allow_prompt=allow_password_prompt,
            prompt_label=f"SSH password for {username}@{host}",
        )

        try:
            material = req.resolve(for_retry=False)
        except PasswordError as e:
            raise ClientError(str(e)) from e

        pw = None if material is None else material.value
        used_password = pw is not None and pw != ""

        def _connect(password_value: str | None) -> None:
            connect_kw: dict[str, Any] = {
                "hostname": host,
                "port": port,
                "username": username,
                "timeout": connect_timeout,
                "look_for_keys": look_for_keys,
                "allow_agent": allow_agent,
                "compress": compress,
            }
            if password_value:
                connect_kw["password"] = password_value
            if key_path:
                connect_kw["key_filename"] = key_path
            self._client.connect(**connect_kw)

        try:
            _connect(pw)
            return
        except paramiko.AuthenticationException as e:
            if used_password:
                # sshpass: second password prompt ⇒ wrong password.
                raise IncorrectPasswordError(
                    f"SSH authentication failed for {username}@{host} "
                    f"(incorrect password)"
                ) from e
        except Exception as e:
            raise ClientError(f"SSH connect failed: {e}") from e

        # No password was used and key/agent failed — try interactive once.
        try:
            retry = req.resolve(for_retry=True)
        except PasswordError as e:
            raise ClientError(
                f"SSH authentication failed for {username}@{host}: {e}"
            ) from e

        if retry is None or not retry.value:
            raise ClientError(
                f"SSH authentication failed for {username}@{host}: "
                f"no password available (set ssh_password or use ssh_key)"
            )

        try:
            _connect(retry.value)
        except paramiko.AuthenticationException as e:
            raise IncorrectPasswordError(
                f"SSH authentication failed for {username}@{host} "
                f"(incorrect password)"
            ) from e
        except Exception as e:
            raise ClientError(f"SSH connect failed: {e}") from e

    @property
    def cwd(self) -> str:
        return self._cwd


    def close(self) -> None:
        try:
            self._sftp.close()
        except Exception:
            pass
        if self._owns_client:
            try:
                self._client.close()
            except Exception:
                pass

    @property
    def is_active(self) -> bool:
        """Whether the underlying SSH transport is still usable."""
        try:
            transport = self._client.get_transport()
            return bool(transport is not None and transport.is_active())
        except Exception:
            return False

    def __enter__(self) -> SshClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def resolve(self, path: str) -> str:
        if path.startswith("/"):
            return posixpath.normpath(path)
        if path == "~":
            return self._home
        if path.startswith("~/"):
            return posixpath.normpath(posixpath.join(self._home, path[2:]))
        return posixpath.normpath(posixpath.join(self._cwd, path))

    def join(self, *parts: str) -> str:
        return posixpath.join(*parts) if parts else ""

    def exists(self, path: str) -> bool:
        try:
            self._sftp.stat(self.resolve(path))
            return True
        except OSError:
            return False

    def is_file(self, path: str) -> bool:
        try:
            mode = self._sftp.stat(self.resolve(path)).st_mode
            return stat.S_ISREG(mode or 0)
        except OSError:
            return False

    def is_dir(self, path: str) -> bool:
        try:
            mode = self._sftp.stat(self.resolve(path)).st_mode
            return stat.S_ISDIR(mode or 0)
        except OSError:
            return False

    def read_bytes(self, path: str) -> bytes:
        resolved = self.resolve(path)
        try:
            with self._sftp.open(resolved, "rb") as f:
                return f.read()
        except OSError as e:
            raise ClientError(f"SSH read_bytes failed: {path}: {e}") from e

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        try:
            return self.read_bytes(path).decode(encoding, errors="replace")
        except (UnicodeError, LookupError) as e:
            raise ClientError(f"SSH read_text failed: {path}: {e}") from e

    def write_bytes(self, path: str, data: bytes) -> None:
        resolved = self.resolve(path)
        parent = posixpath.dirname(resolved)
        if parent and parent != "/":
            self.mkdir(parent, parents=True, exist_ok=True)
        try:
            with self._sftp.open(resolved, "wb") as f:
                f.write(data)
        except OSError as e:
            raise ClientError(f"SSH write_bytes failed: {path}: {e}") from e

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        try:
            data = content.encode(encoding)
        except (UnicodeError, LookupError) as e:
            raise ClientError(f"SSH write_text failed: {path}: {e}") from e
        self.write_bytes(path, data)

    def mkdir(self, path: str, *, parents: bool = True, exist_ok: bool = True) -> None:
        resolved = self.resolve(path)
        if not parents:
            try:
                self._sftp.mkdir(resolved)
            except OSError as e:
                if exist_ok and self.is_dir(resolved):
                    return
                raise ClientError(f"SSH mkdir failed: {path}: {e}") from e
            return

        parts: list[str] = []
        for part in resolved.split("/"):
            if part == "":
                parts.append("")
                continue
            parts.append(part)
            intermediate = "/".join(parts) if parts[0] == "" else posixpath.join(*parts)
            if intermediate in ("", "/"):
                continue
            try:
                self._sftp.mkdir(intermediate)
            except OSError as e:
                if self.is_dir(intermediate):
                    if intermediate == resolved and not exist_ok:
                        raise ClientError(
                            f"SSH mkdir failed: {intermediate} already exists"
                        ) from e
                    continue
                # ``exist_ok`` suppresses only an already-existing directory;
                # permission, quota, and transport errors must remain visible.
                raise ClientError(f"SSH mkdir failed: {intermediate}: {e}") from e

    def delete(self, path: str) -> None:
        resolved = self.resolve(path)
        try:
            self._sftp.remove(resolved)
        except OSError as e:
            raise ClientError(f"SSH delete failed: {path}: {e}") from e

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
        workdir = self.resolve(cwd) if cwd else self.cwd
        try:
            output_limit = validate_max_output_bytes(max_output_bytes)
        except ValueError as e:
            raise ClientError(str(e)) from e
        try:
            timeout = normalize_timeout(timeout)
        except ValueError as e:
            raise ClientError(str(e)) from e
        # The SSH target uses POSIX command-line rules even when this process
        # itself is running on Windows.
        flag_list = normalize_flags(flags, posix=True)
        try:
            env_overrides = normalize_env(env)
        except ValueError as e:
            raise ClientError(str(e)) from e
        try:
            stdin_bytes = None if stdin is None else stdin.encode("utf-8")
        except (AttributeError, UnicodeEncodeError) as e:
            raise ClientError("stdin must be valid UTF-8 text") from e
        use_interpreter = bool(interpreter and interpreter.strip())
        effective_flags = flag_list

        parts: list[str] = [f"cd {self._shell_quote(workdir)}"]
        if env_overrides:
            for key, value in env_overrides.items():
                parts.append(
                    f"export {self._shell_quote(str(key))}={self._shell_quote(str(value))}"
                )

        if use_interpreter:
            interp = interpreter.strip()
            effective_flags = inject_cmd_flag(interp, flag_list)
            inv = [self._shell_quote(interp)]
            inv.extend(self._shell_quote(f) for f in effective_flags)
            inv.append(self._shell_quote(command))
            parts.append(" ".join(inv))
        else:
            parts.append(command)

        remote = " && ".join(parts)
        control_token = secrets.token_hex(8)
        control_prefix = f"__FILE_TOOLS_{control_token}__"
        pgid_inner = (
            f"printf '%s PGID %s\\n' {self._shell_quote(control_prefix)} "
            f'"$$" >&2; exec sh -c {self._shell_quote(remote)}'
        )
        pid_inner = (
            f"printf '%s PID %s\\n' {self._shell_quote(control_prefix)} "
            f'"$$" >&2; exec sh -c {self._shell_quote(remote)}'
        )
        remote = (
            "if command -v setsid >/dev/null 2>&1; then "
            f"exec setsid sh -c {self._shell_quote(pgid_inner)}; "
            "else "
            f"exec sh -c {self._shell_quote(pid_inner)}; "
            "fi"
        )

        extras: dict[str, str] = {}
        if use_interpreter:
            extras["interpreter"] = interpreter.strip()
            if effective_flags:
                extras["flags"] = " ".join(effective_flags)

        t0 = time.monotonic()
        deadline = t0 + timeout if timeout is not None else None
        stdout_buffer = HeadTailBytes(output_limit)
        stderr_buffer = HeadTailBytes(output_limit)
        stderr_probe = bytearray()
        remote_process: tuple[str, int] | None = None
        marker_pattern = re.compile(
            re.escape(control_prefix.encode())
            + rb" (PGID|PID) ([0-9]+)\r?\n"
        )
        channel = None

        def _observe_stderr(chunk: bytes) -> None:
            nonlocal remote_process
            if len(stderr_probe) < 4096:
                stderr_probe.extend(chunk[: 4096 - len(stderr_probe)])
            if remote_process is None:
                match = marker_pattern.search(stderr_probe)
                if match is not None:
                    remote_process = (
                        match.group(1).decode("ascii"),
                        int(match.group(2)),
                    )

        def _strip_control_marker(data: bytes) -> tuple[bytes, int]:
            cleaned, count = marker_pattern.subn(b"", data, count=1)
            removed = len(data) - len(cleaned) if count else 0
            return cleaned, removed

        def _terminate_remote(transport: Any) -> None:
            if remote_process is None:
                return
            mode, pid = remote_process
            target = f"-{pid}" if mode == "PGID" else str(pid)
            grace_tenths = max(1, round(TERMINATION_GRACE_SECS * 10))
            terminate_command = (
                f"kill -TERM -- {target} 2>/dev/null || true; "
                f"i=0; while kill -0 -- {target} 2>/dev/null "
                f"&& [ \"$i\" -lt {grace_tenths} ]; do "
                'sleep 0.1; i=$((i + 1)); done; '
                f"kill -KILL -- {target} 2>/dev/null || true"
            )
            killer = None
            try:
                killer = transport.open_session(timeout=3.0)
                killer.settimeout(3.0)
                killer.exec_command(terminate_command)
                kill_deadline = time.monotonic() + 3.0
                while (
                    not killer.exit_status_ready()
                    and time.monotonic() < kill_deadline
                ):
                    time.sleep(0.01)
            except Exception:
                pass
            finally:
                if killer is not None:
                    try:
                        killer.close()
                    except Exception:
                        pass

        def _result(
            *,
            exit_code: int,
            timed_out: bool,
            stderr_fallback: str = "",
        ) -> CommandResult:
            stdout_bytes = stdout_buffer.display_bytes()
            stderr_bytes = stderr_buffer.display_bytes()
            stderr_bytes, marker_bytes = _strip_control_marker(stderr_bytes)
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            if not stderr_text and stderr_fallback:
                stderr_text = stderr_fallback
            return CommandResult(
                exit_code=exit_code,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_text,
                command=command,
                cwd=workdir,
                duration_ms=int((time.monotonic() - t0) * 1000),
                timed_out=timed_out,
                stdout_total_bytes=stdout_buffer.total_bytes,
                stderr_total_bytes=max(
                    0, stderr_buffer.total_bytes - marker_bytes
                ),
                stdout_omitted_bytes=stdout_buffer.omitted_bytes,
                stderr_omitted_bytes=stderr_buffer.omitted_bytes,
                extras=extras,
            )

        try:
            transport = self._client.get_transport()
            if transport is None:
                raise ClientError("SSH transport is not available")
            channel = transport.open_session(timeout=timeout)
            if self._enable_x11:
                try:
                    channel.request_x11(single_connection=False)
                except Exception:
                    pass
            channel.settimeout(timeout)
            channel.exec_command(remote)
            if stdin_bytes is not None:
                channel.sendall(stdin_bytes)
            # Always signal EOF. Commands that read stdin must not hang when
            # the caller intentionally supplies no input.
            channel.shutdown_write()
            while True:
                if channel.recv_ready():
                    stdout_buffer.append(channel.recv(OUTPUT_READ_CHUNK_BYTES))
                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(OUTPUT_READ_CHUNK_BYTES)
                    _observe_stderr(chunk)
                    stderr_buffer.append(chunk)
                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    _terminate_remote(transport)
                    try:
                        channel.close()
                    except Exception:
                        pass
                    return _result(exit_code=124, timed_out=True)
                sleep_for = 0.01
                if deadline is not None:
                    sleep_for = min(sleep_for, max(0.0, deadline - time.monotonic()))
                time.sleep(sleep_for)
            code = channel.recv_exit_status()
        except Exception as e:
            msg = str(e).lower()
            if timeout is not None and ("timed out" in msg or "timeout" in msg):
                if "transport" in locals() and transport is not None:
                    _terminate_remote(transport)
                if channel is not None:
                    try:
                        channel.close()
                    except Exception:
                        pass
                return _result(
                    exit_code=124,
                    timed_out=True,
                    stderr_fallback=str(e),
                )
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
            raise ClientError(f"SSH exec_command failed: {e}") from e

        try:
            channel.close()
        except Exception:
            pass
        return _result(exit_code=int(code), timed_out=False)

    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> CommandResult:
        return self.exec_command(command, timeout=timeout, cwd=cwd)

    @staticmethod
    def _shell_quote(s: str) -> str:
        return "'" + s.replace("'", "'\"'\"'") + "'"
