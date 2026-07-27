import os
import sys
from pathlib import Path

import pytest

from file_tools import LocalClient
from file_tools._core import inject_cmd_flag
from file_tools.tools.bash import BashError, BashResult, bash


def test_inject_cmd_flag_for_bash() -> None:
    assert inject_cmd_flag("bash", ["-il"]) == ["-il", "-c"]
    assert inject_cmd_flag("bash", ["-ilc"]) == ["-ilc"]
    assert inject_cmd_flag("bash", ["-l", "-c"]) == ["-l", "-c"]
    assert inject_cmd_flag("bash", []) == ["-c"]
    assert inject_cmd_flag("cmd.exe", []) == ["/c"]
    assert inject_cmd_flag("powershell.exe", ["-NoProfile"]) == [
        "-NoProfile",
        "-Command",
    ]
    assert inject_cmd_flag("powershell.exe", ["-EncodedCommand"]) == [
        "-EncodedCommand",
        "-Command",
    ]
    assert inject_cmd_flag("python3.14.exe", []) == ["-c"]


def test_bash_requires_cwd(tmp_path: Path) -> None:
    with pytest.raises(BashError, match="cwd is required"):
        bash("echo hi", cwd="")


def test_bash_requires_command(tmp_path: Path) -> None:
    with pytest.raises(BashError, match="command"):
        bash("  ", cwd=str(tmp_path))


def test_auto_interpreter_selects_the_native_local_shell(tmp_path: Path) -> None:
    result = bash("echo native-shell", cwd=tmp_path)

    assert result.ok
    assert "native-shell" in result.stdout
    expected = "cmd" if os.name == "nt" else "bash"
    assert result.interpreter == expected
    assert result.invocation.startswith(expected)


def test_bash_cwd_is_used(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    (tmp_path / "marker.txt").write_text("x\n")
    r = bash(
        "from pathlib import Path; print(Path('marker.txt').read_text(), end='')",
        cwd=str(tmp_path),
        client=client,
        interpreter=sys.executable,
    )
    assert r.ok
    assert r.stdout.strip() == "x"


def test_bash_nonzero_exit(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    r = bash(
        "raise SystemExit(3)",
        cwd=str(tmp_path),
        client=client,
        interpreter=sys.executable,
    )
    assert not r.ok
    assert r.exit_code == 3


def test_bash_timeout(tmp_path: Path) -> None:
    client = LocalClient(cwd=tmp_path)
    r = bash(
        "import time; time.sleep(5)",
        cwd=str(tmp_path),
        timeout=0.2,
        client=client,
        interpreter=sys.executable,
    )
    assert r.timed_out
    assert not r.ok


def test_bash_env(tmp_path: Path) -> None:
    r = bash(
        "import os; print(os.environ['FT_BASH_TEST'], end='')",
        cwd=str(tmp_path),
        interpreter=sys.executable,
        env={"FT_BASH_TEST": "env-value"},
        client=LocalClient(cwd=tmp_path),
    )
    assert r.ok
    assert r.stdout == "env-value"


def test_bash_rejects_non_mapping_env(tmp_path: Path) -> None:
    with pytest.raises(BashError, match="env must be a mapping"):
        bash("true", cwd=tmp_path, env=["FT_X=value"])  # type: ignore[arg-type]


def test_bash_stdin_and_structured_truncation_metadata(tmp_path: Path) -> None:
    r = bash(
        "import sys; sys.stdout.write(sys.stdin.read()); sys.stdout.write('0' * 5000)",
        cwd=tmp_path,
        interpreter=sys.executable,
        stdin="stdin-value\n",
        max_output_bytes=128,
        client=LocalClient(cwd=tmp_path),
    )

    assert r.ok
    assert r.truncated
    assert r.stdout_total_bytes > 5000
    assert r.stdout_omitted_bytes == r.stdout_total_bytes - 128
    assert "output_truncated:" in r.format()


def test_bash_allows_background_operator(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("background shell syntax is intentionally shell-specific")
    r = bash(
        "printf background-ok & wait",
        cwd=tmp_path,
        client=LocalClient(cwd=tmp_path),
    )
    assert r.ok
    assert r.stdout == "background-ok"


def test_bash_zero_timeout_means_no_timeout(tmp_path: Path) -> None:
    r = bash(
        "print('no-timeout', end='')",
        cwd=tmp_path,
        timeout=0,
        interpreter=sys.executable,
    )
    assert r.ok
    assert r.stdout == "no-timeout"


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_bash_rejects_invalid_timeout(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(BashError, match="timeout"):
        bash("true", cwd=tmp_path, timeout=timeout)


@pytest.mark.parametrize("max_output_bytes", [0, -1, 16 * 1024 * 1024 + 1, 1.5])
def test_bash_rejects_invalid_output_limit(
    tmp_path: Path,
    max_output_bytes: object,
) -> None:
    with pytest.raises(BashError, match="max_output_bytes"):
        bash("true", cwd=tmp_path, max_output_bytes=max_output_bytes)  # type: ignore[arg-type]


def test_bash_format_truncates_middle_and_keeps_stderr_tail() -> None:
    result = BashResult(
        command="generate-output",
        cwd="/tmp",
        exit_code=1,
        stdout="a" * 200,
        stderr="important-tail",
        duration_ms=1,
        timed_out=False,
    )
    text = result.format(max_chars=120)
    assert len(text) == 120
    assert "truncated" in text
    assert text.endswith("important-tail\n")
