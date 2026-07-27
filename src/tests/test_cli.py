import io
import sys
from pathlib import Path

from file_tools.cli.main import main


def test_cli_read_writes_exact_file_content(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("first\nsecond\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "read",
            "note.txt",
            "--cwd",
            str(tmp_path),
            "--offset",
            "2",
            "--no-show-line-numbers",
        ],
        stdin=io.StringIO(),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "second\n"
    assert stderr.getvalue() == ""


def test_cli_write_reads_complete_content_from_stdin(tmp_path: Path) -> None:
    content = "first line\nsecond line\n"
    stdout = io.StringIO()

    exit_code = main(
        ["write", "nested/note.txt", "--cwd", str(tmp_path)],
        stdin=io.StringIO(content),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert (tmp_path / "nested" / "note.txt").read_text() == content
    assert "wrote 23 bytes" in stdout.getvalue()


def test_cli_write_allows_empty_stdin(tmp_path: Path) -> None:
    exit_code = main(
        ["write", "empty.txt", "--cwd", str(tmp_path)],
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert (tmp_path / "empty.txt").read_bytes() == b""


def test_cli_edit_replaces_text(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("before\n")
    stdout = io.StringIO()

    exit_code = main(
        ["edit", "note.txt", "before", "after", "--cwd", str(tmp_path)],
        stdin=io.StringIO(),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert (tmp_path / "note.txt").read_text() == "after\n"
    assert stdout.getvalue().startswith("replaced ")


def test_cli_apply_patch_reads_patch_from_stdin(tmp_path: Path) -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Add File: created.txt\n"
        "+created by cli\n"
        "*** End Patch\n"
    )
    stdout = io.StringIO()

    exit_code = main(
        ["apply_patch", "--cwd", str(tmp_path)],
        stdin=io.StringIO(patch),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert (tmp_path / "created.txt").read_text() == "created by cli\n"
    assert "added=['created.txt']" in stdout.getvalue()


def test_cli_bash_propagates_command_exit_code(tmp_path: Path) -> None:
    stdout = io.StringIO()

    exit_code = main(
        [
            "bash",
            "print('command-output'); raise SystemExit(3)",
            "--cwd",
            str(tmp_path),
            "--interpreter",
            sys.executable,
        ],
        stdin=io.StringIO(),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 3
    assert "exit: 3" in stdout.getvalue()
    assert "command-output" in stdout.getvalue()


def test_cli_bash_reads_stdin_and_accepts_environment_overrides(
    tmp_path: Path,
) -> None:
    stdout = io.StringIO()

    exit_code = main(
        [
            "bash",
            "import os,sys; print(sys.stdin.read(), end=''); "
            "print(os.environ['FT_CLI_ENV'], end='')",
            "--cwd",
            str(tmp_path),
            "--interpreter",
            sys.executable,
            "--stdin",
            "--env",
            "FT_CLI_ENV=env-value",
            "--max-output-bytes",
            "1024",
        ],
        stdin=io.StringIO("stdin-value\n"),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert "stdin-value" in stdout.getvalue()
    assert "env-value" in stdout.getvalue()


def test_cli_tool_error_is_written_to_stderr(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["read", "missing.txt", "--cwd", str(tmp_path)],
        stdin=io.StringIO(),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue().startswith("error: ")
    assert "missing.txt" in stderr.getvalue()
