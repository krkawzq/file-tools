import os
import signal
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from anyio import Event, create_task_group, sleep

from file_tools import (
    ClientError,
    ConflictError,
    LocalClient,
    OperationTimeoutError,
    SshClient,
    TransferLimitError,
    clear_client_cache,
    get_connection_client,
    get_client,
    resolve_client,
)

pytestmark = pytest.mark.anyio


async def test_local_client_exec_command(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = await c.exec_command("echo hi")
    assert r.ok
    assert r.stdout.strip() == "hi"
    assert r.command == "echo hi"
    assert r.cwd == str(tmp_path.resolve())
    assert r.duration_ms >= 0
    assert not r.timed_out


async def test_exec_command_env_and_stdin(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = await c.exec_command(
        "import os,sys; print(os.environ.get('FT_X','')); "
        "print(sys.stdin.read(), end='')",
        interpreter=sys.executable,
        env={"FT_X": "env-ok"},
        stdin="from-stdin\n",
    )
    assert r.ok
    assert "env-ok" in r.stdout
    assert "from-stdin" in r.stdout


async def test_exec_command_timeout(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = await c.exec_command(
        "import time; time.sleep(5)",
        interpreter=sys.executable,
        timeout=0.2,
    )
    assert r.timed_out
    assert not r.ok
    assert r.exit_code == 124


async def test_exec_command_keeps_event_loop_responsive(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    finished = Event()
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while not finished.is_set():
            ticks += 1
            await sleep(0.02)

    async with create_task_group() as task_group:
        task_group.start_soon(ticker)
        result = await client.exec_command(
            "import time; time.sleep(0.25)",
            interpreter=sys.executable,
        )
        finished.set()

    assert result.ok
    assert ticks >= 5


async def test_native_command_releases_gil(tmp_path: Path) -> None:
    from file_tools import _core

    heartbeat = threading.Event()

    def pulse() -> None:
        time.sleep(0.05)
        heartbeat.set()

    thread = threading.Thread(target=pulse)
    thread.start()
    result = _core.LocalClient(cwd=tmp_path).exec_command(
        "import time; time.sleep(0.25)",
        interpreter=sys.executable,
    )
    observed_during_call = heartbeat.is_set()
    thread.join()

    assert result.ok
    assert observed_during_call


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True])
async def test_exec_command_rejects_invalid_timeout(
    tmp_path: Path,
    timeout: object,
) -> None:
    with pytest.raises(ClientError, match="timeout"):
        await LocalClient(cwd=tmp_path).exec_command(
            "echo should-not-start",
            timeout=timeout,  # type: ignore[arg-type]
        )


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="uses Linux /proc and POSIX process groups",
)
async def test_exec_command_timeout_kills_descendant_processes(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = await c.exec_command("sleep 10 & echo $!; wait", timeout=0.3)

    assert r.timed_out
    child_pid = int(r.stdout.strip())
    deadline = time.monotonic() + 2
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        await sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()


async def test_exec_command_replaces_invalid_utf8(tmp_path: Path) -> None:
    r = await LocalClient(cwd=tmp_path).exec_command(
        "import sys; sys.stdout.buffer.write(bytes([255]))",
        interpreter=sys.executable,
    )

    assert r.ok
    assert r.stdout == "\ufffd"
    assert r.stdout_total_bytes == 1


async def test_exec_command_bounds_output_while_preserving_head_and_tail(
    tmp_path: Path,
) -> None:
    r = await LocalClient(cwd=tmp_path).exec_command(
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
async def test_exec_command_bounds_pipe_drain_when_descendant_keeps_it_open(
    tmp_path: Path,
) -> None:
    start = time.monotonic()
    r = await LocalClient(cwd=tmp_path).exec_command("sleep 10 & echo $!")
    child_pid = int(r.stdout.strip())
    try:
        assert r.ok
        assert time.monotonic() - start < 3
    finally:
        try:
            os.kill(child_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


async def test_exec_command_rejects_invalid_environment_name(tmp_path: Path) -> None:
    with pytest.raises(ClientError, match="environment variable name"):
        await LocalClient(cwd=tmp_path).exec_command("true", env={"BAD=NAME": "x"})


async def test_exec_command_rejects_invalid_stdin_before_start(tmp_path: Path) -> None:
    with pytest.raises(ClientError, match="stdin must be valid UTF-8 text"):
        await LocalClient(cwd=tmp_path).exec_command(
            "touch started",
            stdin="\ud800",
        )

    assert not (tmp_path / "started").exists()


async def test_exec_command_nonzero_exit(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = await c.exec_command("raise SystemExit(7)", interpreter=sys.executable)
    assert r.exit_code == 7
    assert not r.ok


async def test_exec_command_interpreter_and_flags(tmp_path: Path) -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    c = LocalClient(cwd=tmp_path)
    r = await c.exec_command("echo hi-from-bash", interpreter="bash", flags="-lc")
    assert r.ok
    assert "hi-from-bash" in r.stdout
    assert r.extras.get("interpreter") == "bash"
    assert r.extras.get("flags") == "-lc"


async def test_exec_command_flags_as_list(tmp_path: Path) -> None:
    c = LocalClient(cwd=tmp_path)
    r = await c.exec_command("print(1+1)", interpreter=sys.executable, flags=["-c"])
    assert r.ok
    assert r.stdout.strip() == "2"
    assert r.extras.get("interpreter") == sys.executable
    assert r.extras.get("flags") == "-c"


async def test_normalize_flags() -> None:
    from file_tools._core import normalize_flags

    assert normalize_flags(None) == []
    assert normalize_flags("") == []
    assert normalize_flags("-lc") == ["-lc"]
    assert normalize_flags("-l -c") == ["-l", "-c"]
    assert normalize_flags(["-l", "-c"]) == ["-l", "-c"]
    assert normalize_flags(r'-File C:\Temp\x.ps1', posix=False) == [
        "-File",
        r"C:\Temp\x.ps1",
    ]


async def test_get_client_creates_fresh_local_clients(tmp_path: Path) -> None:
    a = get_client(client="local", cwd=str(tmp_path))
    b = get_client(client="local", cwd=str(tmp_path))
    assert a is not b
    assert a.kind == "local"
    assert Path(a.cwd) == tmp_path.resolve()


async def test_local_path_info_uses_one_async_operation(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("content")
    client = LocalClient(cwd=tmp_path)

    assert await client.path_info("file.txt") == (True, True, False)
    assert await client.path_info("missing.txt") == (False, False, False)


async def test_local_stat_and_atomic_write_detect_stale_versions(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("before")
    client = LocalClient(cwd=tmp_path)

    initial = await client.stat("file.txt")
    committed = await client.write_text_atomic(
        "file.txt",
        "after",
        expected_version=initial.version,
    )

    assert committed.version != initial.version
    with pytest.raises(ConflictError):
        await client.write_text_atomic(
            "file.txt",
            "stale",
            expected_version=initial.version,
        )
    assert target.read_text() == "after"


async def test_local_full_read_respects_transfer_limit_but_window_is_bounded(
    tmp_path: Path,
) -> None:
    target = tmp_path / "large.txt"
    target.write_text("first\n" + ("x" * 4096) + "\nlast\n")
    client = LocalClient(cwd=tmp_path, max_transfer_bytes=128)

    with pytest.raises(TransferLimitError):
        await client.read_text("large.txt")

    text, total, start, end, truncated = await client.read_text_window(
        "large.txt", 1, 1
    )
    assert text == "first\n"
    assert (total, start, end, truncated) == (3, 1, 1, True)


async def test_named_local_connection_is_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "connections.json"
    config.write_text('{"connections":{"workspace":{"client":"local"}}}')
    monkeypatch.setenv("FILE_TOOLS_CONNECTIONS_FILE", str(config))
    clear_client_cache()

    first = get_connection_client("workspace", cwd=str(tmp_path))
    second = get_connection_client("workspace", cwd=str(tmp_path))

    assert first is second
    clear_client_cache()


async def test_resolve_prefers_explicit_client(tmp_path: Path) -> None:
    explicit = LocalClient(cwd=tmp_path)
    got = resolve_client(explicit, client="local", cwd="/somewhere/else")
    assert got is explicit


async def test_unknown_client_kind() -> None:
    with pytest.raises(ValueError, match="unknown client"):
        get_client(client="ftp")


async def test_ssh_requires_host_port_user() -> None:
    with pytest.raises(ValueError, match="ssh_host is required"):
        get_client(client="ssh", cwd="/tmp", ssh_port=22, ssh_user="u")
    with pytest.raises(ValueError, match="ssh_port is required"):
        get_client(client="ssh", cwd="/tmp", ssh_host="h", ssh_user="u")
    with pytest.raises(ValueError, match="ssh_user is required"):
        get_client(client="ssh", cwd="/tmp", ssh_host="h", ssh_port=22)
    with pytest.raises(ValueError, match="must not start"):
        get_client(
            client="ssh",
            cwd="/tmp",
            ssh_host="-oProxyCommand=touch /tmp/pwned",
            ssh_port=22,
            ssh_user="u",
        )


async def test_python_clients_are_async_facades() -> None:
    import inspect

    import file_tools._core as native

    assert LocalClient is not native.LocalClient
    assert inspect.iscoroutinefunction(LocalClient.read_text)
    assert inspect.iscoroutinefunction(SshClient.exec_command)


@pytest.mark.skipif(os.name != "posix", reason="uses a POSIX fake OpenSSH process")
async def test_native_ssh_construction_does_not_spawn_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import file_tools._core as native

    marker = tmp_path / "ssh-started"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    native.SshClient(
        "fake-host",
        port=22,
        username="test-user",
        cwd=str(tmp_path),
        allow_password_prompt=False,
    )

    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="uses a POSIX fake OpenSSH process")
async def test_native_ssh_runner_preserves_exit_and_kills_timeout_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        "#!/bin/sh\n"
        "for arg in \"$@\"; do remote=$arg; done\n"
        "exec /bin/sh -c \"$remote\"\n"
    )
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    client = SshClient(
        "fake-host",
        port=22,
        username="test-user",
        cwd=str(tmp_path),
        allow_password_prompt=False,
    )
    nonzero = await client.exec_command(
        "printf 'out\\n'; printf 'err\\n' >&2; exit 7",
        interpreter="bash",
    )
    assert nonzero.exit_code == 7
    assert not nonzero.timed_out
    assert nonzero.stdout == "out\n"
    assert nonzero.stderr == "err\n"

    marker = tmp_path / "late-marker"
    timed = await client.exec_command(
        f"(sleep 2; printf late > {marker}) & wait",
        interpreter="bash",
        timeout=0.5,
    )
    assert timed.exit_code == 124
    assert timed.timed_out
    await sleep(2.1)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="uses a POSIX fake OpenSSH process")
async def test_ssh_file_operations_are_bounded_atomic_and_versioned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        "#!/bin/sh\n"
        "for arg in \"$@\"; do remote=$arg; done\n"
        "exec /bin/sh -c \"$remote\"\n"
    )
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    target = tmp_path / "remote.txt"
    target.write_text("a\nb\nc")
    client = SshClient(
        "fake-host",
        port=22,
        username="test-user",
        cwd=str(tmp_path),
        allow_password_prompt=False,
        multiplexing=False,
    )

    initial = await client.stat("remote.txt")
    window = await client.read_text_window("remote.txt", 2, 1)
    committed = await client.write_text_atomic(
        "remote.txt",
        "changed\n",
        expected_version=initial.version,
    )

    assert initial.kind == "file"
    assert window == ("b\n", 3, 2, 2, True)
    assert committed.version != initial.version
    with pytest.raises(ConflictError):
        await client.write_text_atomic(
            "remote.txt",
            "stale\n",
            expected_version=initial.version,
        )
    assert target.read_text() == "changed\n"
    assert not list(tmp_path.glob("*.file-tools.lock"))
    assert not list(tmp_path.glob("*.file-tools-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="uses a POSIX fake OpenSSH process")
async def test_ssh_file_operation_timeout_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text("#!/bin/sh\nsleep 5\n")
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    client = SshClient(
        "fake-host",
        port=22,
        username="test-user",
        cwd=str(tmp_path),
        operation_timeout=0.1,
        allow_password_prompt=False,
        multiplexing=False,
    )

    with pytest.raises(OperationTimeoutError):
        await client.stat("remote.txt")
