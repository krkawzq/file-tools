"""Local terminal client."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

from ._output import (
    DEFAULT_MAX_OUTPUT_BYTES,
    OUTPUT_DRAIN_TIMEOUT_SECS,
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



class LocalClient:
    """Local disk + local shell."""

    kind = "local"

    def __init__(self, cwd: str | os.PathLike[str] | None = None) -> None:
        if cwd is None or cwd == "":
            self._cwd = Path.cwd().resolve()
        else:
            self._cwd = Path(cwd).expanduser().resolve()

    @property
    def cwd(self) -> str:
        return str(self._cwd)

    def resolve(self, path: str) -> str:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self._cwd / p
        try:
            return str(p.resolve())
        except OSError:
            return str(p.absolute())

    def exists(self, path: str) -> bool:
        return Path(self.resolve(path)).exists()

    def is_file(self, path: str) -> bool:
        return Path(self.resolve(path)).is_file()

    def is_dir(self, path: str) -> bool:
        return Path(self.resolve(path)).is_dir()

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        try:
            # Decode bytes directly so Python's universal-newline text layer
            # does not silently turn CRLF into LF before edit/patch logic can
            # preserve the file's original convention.
            return Path(self.resolve(path)).read_bytes().decode(
                encoding,
                errors="replace",
            )
        except (OSError, UnicodeError, LookupError) as e:
            raise ClientError(f"read_text failed: {path}: {e}") from e

    def read_bytes(self, path: str) -> bytes:
        try:
            return Path(self.resolve(path)).read_bytes()
        except OSError as e:
            raise ClientError(f"read_bytes failed: {path}: {e}") from e

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        resolved = self.resolve(path)
        p = Path(resolved)
        try:
            # Encode before opening the target. TextIO opens/truncates first, so
            # an encoding failure could otherwise destroy the existing file.
            data = content.encode(encoding)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        except (OSError, UnicodeError, LookupError) as e:
            raise ClientError(f"write_text failed: {path}: {e}") from e

    def write_bytes(self, path: str, data: bytes) -> None:
        resolved = self.resolve(path)
        p = Path(resolved)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        except OSError as e:
            raise ClientError(f"write_bytes failed: {path}: {e}") from e

    def mkdir(self, path: str, *, parents: bool = True, exist_ok: bool = True) -> None:
        try:
            Path(self.resolve(path)).mkdir(parents=parents, exist_ok=exist_ok)
        except OSError as e:
            raise ClientError(f"mkdir failed: {path}: {e}") from e

    def delete(self, path: str) -> None:
        p = Path(self.resolve(path))
        try:
            p.unlink()
        except OSError as e:
            raise ClientError(f"delete failed: {path}: {e}") from e

    def join(self, *parts: str) -> str:
        if not parts:
            return ""
        return str(Path(parts[0]).joinpath(*parts[1:]))

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
        try:
            env_overrides = normalize_env(env)
        except ValueError as e:
            raise ClientError(str(e)) from e
        try:
            stdin_bytes = None if stdin is None else stdin.encode("utf-8")
        except (AttributeError, UnicodeEncodeError) as e:
            raise ClientError("stdin must be valid UTF-8 text") from e
        proc_env = None
        if env_overrides:
            proc_env = os.environ.copy()
            if os.name == "nt":
                # Windows treats environment names as case-insensitive.
                existing = {key.casefold(): key for key in proc_env}
                for key, value in env_overrides.items():
                    previous = existing.get(key.casefold())
                    if previous is not None and previous != key:
                        proc_env.pop(previous, None)
                    proc_env[key] = value
                    existing[key.casefold()] = key
            else:
                proc_env.update(env_overrides)

        flag_list = normalize_flags(flags, posix=os.name != "nt")
        use_interpreter = bool(interpreter and interpreter.strip())
        if use_interpreter:
            interp = interpreter.strip()
            effective_flags = inject_cmd_flag(interp, flag_list)
            argv: list[str] | str = [interp, *effective_flags, command]
            shell = False
        else:
            argv = command
            shell = True

        extras: dict[str, str] = {}
        if use_interpreter:
            extras["interpreter"] = interp
            if effective_flags:
                extras["flags"] = " ".join(effective_flags)

        stdout_buffer = HeadTailBytes(output_limit)
        stderr_buffer = HeadTailBytes(output_limit)
        t0 = time.monotonic()
        process: subprocess.Popen[bytes] | None = None

        def _drain(
            stream: object,
            output: HeadTailBytes,
        ) -> None:
            read = getattr(stream, "read")
            try:
                while True:
                    chunk = read(OUTPUT_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    output.append(chunk)
            except (OSError, ValueError):
                # The main thread may close the pipe after the bounded drain
                # deadline when a detached descendant keeps it open.
                return

        def _write_stdin(stream: object, content: bytes) -> None:
            try:
                getattr(stream, "write")(content)
                getattr(stream, "flush")()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    getattr(stream, "close")()
                except (OSError, ValueError):
                    pass

        def _terminate_tree(proc: subprocess.Popen[bytes]) -> None:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    return
                except OSError:
                    proc.terminate()
                grace_deadline = time.monotonic() + TERMINATION_GRACE_SECS
                while time.monotonic() < grace_deadline:
                    try:
                        os.killpg(proc.pid, 0)
                    except ProcessLookupError:
                        break
                    except OSError:
                        break
                    time.sleep(0.01)
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        proc.kill()
                try:
                    proc.wait(timeout=OUTPUT_DRAIN_TIMEOUT_SECS)
                except subprocess.TimeoutExpired:
                    pass
                return
            else:
                if proc.poll() is not None:
                    return
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except (AttributeError, OSError):
                    proc.terminate()
            try:
                proc.wait(timeout=TERMINATION_GRACE_SECS)
                return
            except subprocess.TimeoutExpired:
                pass
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
                except OSError:
                    proc.kill()
            else:
                # taskkill is the standard-library-compatible way to terminate
                # descendants on Windows when no Job Object wrapper is present.
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                except OSError:
                    # Minimal Windows images may not provide taskkill.
                    pass
                if proc.poll() is None:
                    proc.kill()
            try:
                proc.wait(timeout=OUTPUT_DRAIN_TIMEOUT_SECS)
            except subprocess.TimeoutExpired:
                pass

        try:
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
            process = subprocess.Popen(
                argv,
                shell=shell,
                cwd=workdir,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env,
                start_new_session=os.name == "posix",
                creationflags=creationflags,
            )
        except (OSError, ValueError) as e:
            raise ClientError(f"command failed to start: {e}") from e

        assert process.stdout is not None
        assert process.stderr is not None
        if stdin is not None:
            assert process.stdin is not None
            threading.Thread(
                target=_write_stdin,
                args=(process.stdin, stdin_bytes),
                daemon=True,
                name="file-tools-stdin",
            ).start()

        if os.name == "posix":
            selector = selectors.DefaultSelector()
            streams = (
                (process.stdout, stdout_buffer),
                (process.stderr, stderr_buffer),
            )
            for stream, output in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, output)

            timed_out = False
            return_code: int | None = None
            command_deadline = t0 + timeout if timeout is not None else None
            drain_deadline: float | None = None
            while return_code is None or selector.get_map():
                now = time.monotonic()
                if return_code is None:
                    polled = process.poll()
                    if polled is not None:
                        return_code = polled
                        drain_deadline = now + OUTPUT_DRAIN_TIMEOUT_SECS
                    elif command_deadline is not None and now >= command_deadline:
                        timed_out = True
                        _terminate_tree(process)
                        return_code = 124
                        drain_deadline = (
                            time.monotonic() + OUTPUT_DRAIN_TIMEOUT_SECS
                        )

                if drain_deadline is not None and now >= drain_deadline:
                    break

                wait_for = 0.05
                active_deadline = (
                    command_deadline if return_code is None else drain_deadline
                )
                if active_deadline is not None:
                    wait_for = min(
                        wait_for,
                        max(0.0, active_deadline - time.monotonic()),
                    )
                for key, _ in selector.select(wait_for):
                    stream = key.fileobj
                    output = key.data
                    try:
                        chunk = os.read(stream.fileno(), OUTPUT_READ_CHUNK_BYTES)
                    except BlockingIOError:
                        continue
                    except OSError:
                        chunk = b""
                    if chunk:
                        output.append(chunk)
                    else:
                        selector.unregister(stream)
                        stream.close()

            for key in list(selector.get_map().values()):
                try:
                    selector.unregister(key.fileobj)
                except Exception:
                    pass
                try:
                    key.fileobj.close()
                except (OSError, ValueError):
                    pass
            selector.close()
            if return_code is None:
                return_code = process.wait()
        else:
            readers = [
                threading.Thread(
                    target=_drain,
                    args=(process.stdout, stdout_buffer),
                    daemon=True,
                    name="file-tools-stdout",
                ),
                threading.Thread(
                    target=_drain,
                    args=(process.stderr, stderr_buffer),
                    daemon=True,
                    name="file-tools-stderr",
                ),
            ]
            for reader in readers:
                reader.start()

            timed_out = False
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_tree(process)
                return_code = 124

            drain_deadline = time.monotonic() + OUTPUT_DRAIN_TIMEOUT_SECS
            for reader in readers:
                remaining = max(0.0, drain_deadline - time.monotonic())
                reader.join(remaining)
            # Windows pipe reads cannot be selected. Reader threads are daemon
            # threads so a detached descendant cannot block the caller forever.

        duration_ms = int((time.monotonic() - t0) * 1000)
        process_signal = (
            -return_code if not timed_out and return_code < 0 else None
        )
        return CommandResult(
            exit_code=124 if timed_out else int(return_code),
            stdout=stdout_buffer.text(),
            stderr=stderr_buffer.text(),
            command=command,
            cwd=workdir,
            duration_ms=duration_ms,
            timed_out=timed_out,
            signal=process_signal,
            stdout_total_bytes=stdout_buffer.total_bytes,
            stderr_total_bytes=stderr_buffer.total_bytes,
            stdout_omitted_bytes=stdout_buffer.omitted_bytes,
            stderr_omitted_bytes=stderr_buffer.omitted_bytes,
            extras=extras,
        )


    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> CommandResult:
        """Alias for :meth:`exec_command`."""
        return self.exec_command(command, timeout=timeout, cwd=cwd)
