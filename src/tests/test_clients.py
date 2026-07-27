import os
import posixpath
import signal
import shutil
import subprocess
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from file_tools.client import LocalClient, clear_client_cache, get_client, resolve_client
from file_tools.client.base import ClientError
from file_tools.client.ssh import SshClient


def test_local_client_exec_command(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = c.exec_command("echo hi")
    assert r.ok
    assert r.stdout.strip() == "hi"
    assert r.command == "echo hi"
    assert r.cwd == str(tmp_path.resolve())
    assert r.duration_ms >= 0
    assert not r.timed_out


def test_exec_command_env_and_stdin(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = c.exec_command(
        "import os,sys; print(os.environ.get('FT_X','')); "
        "print(sys.stdin.read(), end='')",
        interpreter=sys.executable,
        env={"FT_X": "env-ok"},
        stdin="from-stdin\n",
    )
    assert r.ok
    assert "env-ok" in r.stdout
    assert "from-stdin" in r.stdout


def test_exec_command_timeout(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = c.exec_command(
        "import time; time.sleep(5)",
        interpreter=sys.executable,
        timeout=0.2,
    )
    assert r.timed_out
    assert not r.ok
    assert r.exit_code == 124


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True])
def test_exec_command_rejects_invalid_timeout(
    tmp_path: Path,
    timeout: object,
) -> None:
    with pytest.raises(ClientError, match="timeout"):
        LocalClient(cwd=tmp_path).exec_command(
            "echo should-not-start",
            timeout=timeout,  # type: ignore[arg-type]
        )


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="uses Linux /proc and POSIX process groups",
)
def test_exec_command_timeout_kills_descendant_processes(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = c.exec_command("sleep 10 & echo $!; wait", timeout=0.3)

    assert r.timed_out
    child_pid = int(r.stdout.strip())
    deadline = time.monotonic() + 2
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()


def test_exec_command_replaces_invalid_utf8(tmp_path: Path) -> None:
    r = LocalClient(cwd=tmp_path).exec_command(
        "import sys; sys.stdout.buffer.write(bytes([255]))",
        interpreter=sys.executable,
    )

    assert r.ok
    assert r.stdout == "\ufffd"
    assert r.stdout_total_bytes == 1


def test_exec_command_bounds_output_while_preserving_head_and_tail(
    tmp_path: Path,
) -> None:
    r = LocalClient(cwd=tmp_path).exec_command(
        "import sys; sys.stdout.write('0' * 5000)",
        interpreter=sys.executable,
        max_output_bytes=128,
    )

    assert r.ok
    assert r.truncated
    assert r.stdout_total_bytes == 5000
    assert r.stdout_omitted_bytes == 5000 - 128
    assert "bytes omitted" in r.stdout
    assert r.stdout.startswith("0" * 64)
    assert r.stdout.endswith("0" * 64)


@pytest.mark.skipif(os.name != "posix", reason="uses POSIX process cleanup")
def test_exec_command_bounds_pipe_drain_when_descendant_keeps_it_open(
    tmp_path: Path,
) -> None:
    start = time.monotonic()
    r = LocalClient(cwd=tmp_path).exec_command("sleep 10 & echo $!")
    child_pid = int(r.stdout.strip())
    try:
        assert r.ok
        assert time.monotonic() - start < 3
    finally:
        try:
            os.kill(child_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def test_exec_command_rejects_invalid_environment_name(tmp_path: Path) -> None:
    with pytest.raises(ClientError, match="environment variable name"):
        LocalClient(cwd=tmp_path).exec_command("true", env={"BAD=NAME": "x"})


def test_exec_command_rejects_invalid_stdin_before_start(tmp_path: Path) -> None:
    with pytest.raises(ClientError, match="stdin must be valid UTF-8 text"):
        LocalClient(cwd=tmp_path).exec_command(
            "touch started",
            stdin="\ud800",
        )

    assert not (tmp_path / "started").exists()


def test_exec_command_nonzero_exit(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = c.exec_command("raise SystemExit(7)", interpreter=sys.executable)
    assert r.exit_code == 7
    assert not r.ok


def test_exec_command_interpreter_and_flags(tmp_path: Path) -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    c = LocalClient(cwd=tmp_path)
    # bash -lc 'echo $0' → prints bash
    r = c.exec_command("echo hi-from-bash", interpreter="bash", flags="-lc")
    assert r.ok
    assert "hi-from-bash" in r.stdout
    assert r.extras.get("interpreter") == "bash"
    assert r.extras.get("flags") == "-lc"


def test_exec_command_flags_as_list(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = c.exec_command("print(1+1)", interpreter=sys.executable, flags=["-c"])
    assert r.ok
    assert r.stdout.strip() == "2"
    assert r.extras.get("interpreter") == sys.executable
    assert r.extras.get("flags") == "-c"


def test_normalize_flags() -> None:
    from file_tools.client import normalize_flags

    assert normalize_flags(None) == []
    assert normalize_flags("") == []
    assert normalize_flags("-lc") == ["-lc"]
    assert normalize_flags("-l -c") == ["-l", "-c"]
    assert normalize_flags(["-l", "-c"]) == ["-l", "-c"]
    assert normalize_flags(r'-File C:\Temp\x.ps1', posix=False) == [
        "-File",
        r"C:\Temp\x.ps1",
    ]


def test_get_client_defaults_local(tmp_path: Path) -> None:
    clear_client_cache()
    a = get_client(client="local", cwd=str(tmp_path))
    b = get_client(client="local", cwd=str(tmp_path))
    assert a is b  # LRU cache hit
    assert a.kind == "local"
    assert Path(a.cwd) == tmp_path.resolve()


def test_resolve_prefers_explicit_client(tmp_path: Path) -> None:
    explicit = LocalClient(cwd=tmp_path)
    got = resolve_client(explicit, client="local", cwd="/somewhere/else")
    assert got is explicit


def test_unknown_client_type() -> None:
    clear_client_cache()
    with pytest.raises(ValueError, match="unknown client"):
        get_client(client="ftp")


def test_ssh_requires_host_port_user() -> None:
    clear_client_cache()
    with pytest.raises(ValueError, match="ssh_host is required"):
        get_client(client="ssh", cwd="/tmp", ssh_port=22, ssh_user="u")
    with pytest.raises(ValueError, match="ssh_port is required"):
        get_client(client="ssh", cwd="/tmp", ssh_host="h", ssh_user="u")
    with pytest.raises(ValueError, match="ssh_user is required"):
        get_client(client="ssh", cwd="/tmp", ssh_host="h", ssh_port=22)




class _FakeSftp:
    def __init__(self) -> None:
        self.directories = {"/", "/home", "/home/user"}

    def mkdir(self, path: str) -> None:
        if path in self.directories:
            raise OSError("already exists")
        parent = posixpath.dirname(path)
        if parent not in self.directories:
            raise OSError("parent missing")
        self.directories.add(path)

    def stat(self, path: str) -> SimpleNamespace:
        if path not in self.directories:
            raise OSError("not found")
        return SimpleNamespace(st_mode=stat.S_IFDIR)


def test_ssh_mkdir_allows_existing_parents_when_exist_ok_is_false() -> None:
    client = SshClient.__new__(SshClient)
    client._cwd = "/home/user"
    client._home = "/home/user"
    client._sftp = _FakeSftp()

    client.mkdir("nested/leaf", parents=True, exist_ok=False)
    assert "/home/user/nested/leaf" in client._sftp.directories

    with pytest.raises(ClientError, match="SSH mkdir failed"):
        client.mkdir("nested/leaf", parents=True, exist_ok=False)


def test_ssh_mkdir_does_not_hide_real_errors_when_exist_ok_is_true() -> None:
    class FailingSftp(_FakeSftp):
        def mkdir(self, path: str) -> None:
            if path == "/home/user/blocked":
                raise OSError("permission denied")
            super().mkdir(path)

    client = SshClient.__new__(SshClient)
    client._cwd = "/home/user"
    client._home = "/home/user"
    client._sftp = FailingSftp()

    with pytest.raises(ClientError, match="permission denied"):
        client.mkdir("blocked", parents=True, exist_ok=True)


def test_ssh_text_encoding_errors_are_wrapped() -> None:
    client = SshClient.__new__(SshClient)

    with pytest.raises(ClientError, match="SSH write_text failed"):
        client.write_text("ignored", "content", encoding="not-an-encoding")


def test_ssh_resolve_expands_home() -> None:
    client = SshClient.__new__(SshClient)
    client._cwd = "/work/project"
    client._home = "/home/user"

    assert client.resolve("~") == "/home/user"
    assert client.resolve("~/data/file.txt") == "/home/user/data/file.txt"


class _FakeChannel:
    def __init__(
        self,
        *,
        exits: bool = True,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.command = ""
        self.stdin_closed = False
        self.closed = False
        self._exits = exits
        self.stdout = bytearray(stdout)
        self.stderr = bytearray(stderr)

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def exec_command(self, command: str) -> None:
        self.command = command

    def sendall(self, data: bytes) -> None:
        self.stdin = data

    def shutdown_write(self) -> None:
        self.stdin_closed = True

    def recv_ready(self) -> bool:
        return bool(self.stdout)

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr)

    def recv(self, size: int) -> bytes:
        data = bytes(self.stdout[:size])
        del self.stdout[:size]
        return data

    def recv_stderr(self, size: int) -> bytes:
        data = bytes(self.stderr[:size])
        del self.stderr[:size]
        return data

    def exit_status_ready(self) -> bool:
        return self._exits

    def recv_exit_status(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


class _FakeTransport:
    def __init__(self, channel: _FakeChannel) -> None:
        self.channel = channel

    def open_session(self, timeout: float | None = None) -> _FakeChannel:
        return self.channel


class _FakeSshConnection:
    def __init__(self, channel: _FakeChannel) -> None:
        self.transport = _FakeTransport(channel)

    def get_transport(self) -> _FakeTransport:
        return self.transport


def _fake_ssh_exec_client(channel: _FakeChannel) -> SshClient:
    client = SshClient.__new__(SshClient)
    client._cwd = "/work/project"
    client._home = "/home/user"
    client._client = _FakeSshConnection(channel)
    client._enable_x11 = False
    return client


def test_ssh_exec_injects_command_flag_and_closes_stdin() -> None:
    channel = _FakeChannel()
    client = _fake_ssh_exec_client(channel)

    result = client.exec_command("printf remote-ok", interpreter="bash", flags="-l")

    assert result.ok
    assert "setsid" in channel.command
    assert "printf remote-ok" in channel.command
    assert result.extras["flags"] == "-l -c"
    assert channel.stdin_closed


def test_ssh_exec_enforces_timeout_without_channel_output() -> None:
    channel = _FakeChannel(exits=False)
    client = _fake_ssh_exec_client(channel)

    result = client.exec_command("sleep forever", timeout=0.02)

    assert result.timed_out
    assert result.exit_code == 124
    assert channel.closed


def test_ssh_exec_bounds_output() -> None:
    channel = _FakeChannel(stdout=b"a" * 5000, stderr=b"b" * 5000)
    client = _fake_ssh_exec_client(channel)

    result = client.exec_command("produce-output", max_output_bytes=128)

    assert result.truncated
    assert result.stdout_total_bytes == 5000
    assert result.stderr_total_bytes == 5000
    assert result.stdout_omitted_bytes == 5000 - 128
    assert result.stderr_omitted_bytes == 5000 - 128
    assert result.stdout.startswith("a" * 64)
    assert result.stdout.endswith("a" * 64)
    assert result.stderr.startswith("b" * 64)
    assert result.stderr.endswith("b" * 64)


def test_ssh_wrapper_command_executes_with_expected_shell_semantics(
    tmp_path: Path,
) -> None:
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("requires a local POSIX shell to validate the SSH wrapper")
    channel = _FakeChannel()
    client = _fake_ssh_exec_client(channel)
    client._cwd = str(tmp_path)

    client.exec_command(
        "printf '%s' \"$FT_REMOTE_TEST\"",
        interpreter="bash",
        env={"FT_REMOTE_TEST": "remote-ok"},
    )
    completed = subprocess.run(
        ["sh", "-c", channel.command],
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == b"remote-ok"
    assert b"__FILE_TOOLS_" in completed.stderr
